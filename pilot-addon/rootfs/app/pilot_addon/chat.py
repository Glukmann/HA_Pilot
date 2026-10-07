"""Chat: the Pilot as a conversational agent — the "пульт" (remote control).

One owner message in -> one LLM call (external tokens, the active model
profile) -> strict JSON:

    {"say": "Готово, свет в гостиной выключен.",
     "actions": [{"domain": "light", "service": "turn_off",
                  "entity_id": "light.living", "data": {}}]}

The HA integration executes each action by its ``mode`` from safety.py:
"direct" immediately, "queue" becomes a trust-queue proposal (owner
confirms in the workshop), "refuse" is dropped. The budget guard shares
the daily limit with the supervisor; failures answer with owner-facing
``say`` text, never with an empty chat.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .safety import classify
from .state import RuntimeState
from .supervisor import _accrue_cost, _ask_llm, load_supervisor_config

logger = logging.getLogger("pilot.addon")

MAX_VITRINE_LINES = 300

CHAT_SYSTEM_PROMPT = """Ты — Пилот, голосовой жилец умного дома. Хозяин пишет \
тебе из чата Home Assistant. У тебя есть актуальная карта дома (витрина).

Жёсткие правила:
- Отвечай СТРОГО одним JSON-объектом, без текста вне JSON.
- Поле "say" — твоя короткая реплика хозяину (1-2 предложения, на русском).
- Поле "actions" — список сервис-вызовов HA, которые нужны для просьбы \
хозяина. Пустой список, если хозяин просто спрашивает.
- Каждый вызов: {"domain", "service", "entity_id", "data" (можно пустой)}.
  Только сущности из карты дома выше. Не выдумывай entity_id.
- Ты НЕ исполняешь действия сам — их исполнит рантайм по своим правилам \
безопасности. Камеры и охрану не трогай вообще.
- Если просьба неоднозначна — не действуй, уточни в "say".

Формат ответа:
{"say": "…", "actions": []}"""


async def chat_ask(
    state: RuntimeState,
    message: str,
    *,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Answer one chat message; never raises, always something to say."""
    state.reset_cost_if_new_day()
    audit = state.queue._audit
    base: dict[str, Any] = {"say": "", "actions": []}

    config = load_supervisor_config(state)
    if config is None:
        audit.record("chat.skipped", {"reason": "no_config"})
        return {
            **base,
            "say": "Я ещё не настроен: задайте модель в мастерской, раздел «Модели».",
            "error": "no_config",
        }
    if state.cost_today >= state.daily_budget:
        audit.record("chat.skipped", {"reason": "budget"})
        return {
            **base,
            "say": (
                f"Дневной лимит ₽{state.daily_budget:.0f} исчерпан, подожду до завтра."
            ),
            "error": "budget",
        }

    all_lines = state.vitrine.lines
    lines = all_lines[-MAX_VITRINE_LINES:]
    header = "Карта дома (витрина):"
    if len(lines) < len(all_lines):
        header += (
            f" (показаны последние {len(lines)} из {len(all_lines)} строк —"
            " дом больше окна контекста)"
        )
    user_message = "\n".join(
        [header, *lines, "", f"Сообщение хозяина: {message}"]
    )
    own_session = http_session is None
    session = http_session or aiohttp.ClientSession()
    try:
        try:
            content, usage = await _ask_llm(
                session, config, CHAT_SYSTEM_PROMPT, user_message
            )
        except Exception as err:
            audit.record("chat.error", {"error": str(err)})
            return {
                **base,
                "say": "Не смог дозвониться до модели, попробуйте ещё раз.",
                "error": "llm",
            }
        tokens_in, tokens_out, cost = _accrue_cost(state, config, usage)
        try:
            say, actions = _parse_answer(content)
        except ValueError as err:
            audit.record("chat.parse_error", {"error": str(err)})
            return {
                **base,
                "say": "Модель ответила непонятно, попробуйте иначе.",
                "error": "parse_error",
            }
        planned = [{**action, "mode": classify(action)} for action in actions]
        audit.record(
            "chat.ask",
            {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": round(cost, 6),
                "actions": len(actions),
                "direct": sum(1 for a in planned if a["mode"] == "direct"),
                "queued": sum(1 for a in planned if a["mode"] == "queue"),
            },
        )
        return {"say": say, "actions": planned, "cost": round(cost, 6)}
    finally:
        if own_session:
            await session.close()


def _parse_answer(content: str) -> tuple[str, list[dict[str, Any]]]:
    """Strict-parse the LLM JSON into (say, actions); ValueError when broken."""
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json")
        text = text.removeprefix("```")
        text = text.removesuffix("```").strip()
    raw = json.loads(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("say"), str):
        raise ValueError("say required")
    say = raw["say"].strip()
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ValueError("actions must be a list")
    actions: list[dict[str, Any]] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        domain = item.get("domain")
        service = item.get("service")
        entity_id = item.get("entity_id")
        if not (
            isinstance(domain, str)
            and isinstance(service, str)
            and isinstance(entity_id, str)
            and "." in entity_id
        ):
            continue
        data = item.get("data")
        actions.append(
            {
                "domain": domain,
                "service": service,
                "entity_id": entity_id,
                "data": data if isinstance(data, dict) else {},
            }
        )
    return say, actions
