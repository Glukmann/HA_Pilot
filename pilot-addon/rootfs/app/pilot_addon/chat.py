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
- Поле "say" — твоя реплика хозяину (на русском, длина — по стилю ниже).
- Поле "actions" — список сервис-вызовов HA, которые нужны для просьбы \
хозяина. Пустой список, если хозяин просто спрашивает.
- Каждый вызов: {"domain", "service", "entity_id", "data" (можно пустой)}.
  Только сущности из карты дома выше. Не выдумывай entity_id.
- Значения с % — влажность или заряд батарейки датчика; названия вида
  «…Батарея», «…Влажность», «…Sampling interval» — НЕ температура и НЕ
  состояние устройства. Отвечай про устройство по его основной сущности.
- Показатели самих устройств (роутер: «2.4 ГГц», «5 ГГц», нагрев радио,
  CPU, память, сигнал, аптайм; розетки: мощность, энергия; реле:
  countdown) — это диагностика железа, а НЕ климат комнаты и не состояние
  комнаты. Никогда не выдавай их за температуру/влажность воздуха.
- Если в комнате нет подходящей сущности (датчика температуры воздуха,
  освещённости и т.п.) — честно скажи: «датчика в <комнате> нет». НЕ
  подставляй ближайшее совпадение по названию — лучше признайся.
- Ты НЕ исполняешь действия сам — их исполнит рантайм по своим правилам \
безопасности. Камеры и охрану не трогай вообще.
- Используй недавний диалог ниже: «тот же», «его», «а ещё» отсылают к нему.
- Если просьба неоднозначна — не действуй, уточни в "say".

Формат ответа:
{"say": "…", "actions": []}"""

PRESET_NAMES = {
    "butler": "Дворецкий",
    "observer": "Тихий наблюдатель",
    "economy": "Эконом",
}
MODE_NAMES = {
    "normal": "обычный",
    "vacation": "отпуск (дом пустует)",
    "guests": "гости",
    "sick": "кто-то болеет",
}


def _persona_block(state: RuntimeState) -> str:
    """Persona/mode/focus appended to the system prompt — the voice tuning."""
    verbosity = state.persona.get("verbosity", 50)
    if verbosity < 30:
        style = "Отвечай предельно коротко: одно предложение, без вступлений."
    elif verbosity < 70:
        style = "Отвечай кратко: 1–2 предложения по существу."
    else:
        style = "Отвечай развёрнуто: контекст, детали, что предпринято."
    lines = [
        "",
        "Стиль и обстановка:",
        "- Пресет персоны: "
        f"«{PRESET_NAMES.get(state.persona_preset, state.persona_preset)}» "
        f"(дворецкий {state.persona.get('butler_observer', 50)}/100, "
        f"вежливость {state.persona.get('politeness', 50)}/100, "
        f"осторожность {state.persona.get('conservative', 50)}/100).",
        f"- Режим дома: {MODE_NAMES.get(state.mode, state.mode)}.",
        style,
    ]
    if state.current_focus.strip():
        lines.append(f"- Текущий фокус хозяина: {state.current_focus.strip()}.")
    return "\n".join(lines)


def _history_block(state: RuntimeState, conversation_id: str) -> str:
    """Render the recent turns of this conversation for the prompt."""
    turns = state.sessions.history(conversation_id)
    if not turns:
        return ""
    who = {"user": "Хозяин", "assistant": "Пилот"}
    lines = ["Недавний диалог:", *[f"{who[t['role']]}: {t['text']}" for t in turns]]
    return "\n".join(lines)


_OWNER_MEMORY_REJECTS = 8
_OWNER_MEMORY_AUDIT_TAIL = 300


def _owner_memory_block(state: RuntimeState) -> str:
    """Learning stats + recent rejections — the owner's decision history.

    Lets the chat reason about habits ("в прошлый раз ты отклонил…")
    instead of asking the same thing twice. Rejections are read from the
    append-only audit tail; titles only, no secrets.
    """
    learning = state.queue.as_learning()
    total = learning["total"]
    lines = [
        "История решений хозяина:",
        (
            f"- Всего: предложено {total['proposed']}, принято {total['accepted']}, "
            f"отклонено {total['rejected']}, применено молча {total['applied_silent']}."
        ),
    ]
    kinds = sorted(
        learning["by_kind"].items(),
        key=lambda kv: kv[1]["accepted"] + kv[1]["rejected"],
        reverse=True,
    )[:5]
    if kinds:
        detail = "; ".join(
            f"{kind}: принято {v['accepted']}/отклонено {v['rejected']}"
            for kind, v in kinds
        )
        lines.append(f"- По видам действий: {detail}.")
    rejections = _recent_rejection_titles(state)
    if rejections:
        lines.append("- Недавние отказы:")
        lines.extend(f"  - {title}" for title in rejections)
    return "\n".join(lines)


def _recent_rejection_titles(state: RuntimeState) -> list[str]:
    """The last rejected proposal titles from the audit tail."""
    path = state.queue._audit.path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    titles: list[str] = []
    for line in text.splitlines()[-_OWNER_MEMORY_AUDIT_TAIL:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") != "queue.rejected":
            continue
        detail = record.get("detail")
        if isinstance(detail, dict) and detail.get("title"):
            titles.append(str(detail["title"]))
    return titles[-_OWNER_MEMORY_REJECTS:]


async def chat_ask(
    state: RuntimeState,
    message: str,
    *,
    conversation_id: str | None = None,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Answer one chat message; never raises, always something to say."""
    state.reset_cost_if_new_day()
    audit = state.queue._audit
    base: dict[str, Any] = {"say": "", "actions": []}
    conversation_id = conversation_id or "default"

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
        part
        for part in [
            header,
            *lines,
            "",
            _owner_memory_block(state),
            _history_block(state, conversation_id),
            f"Сообщение хозяина: {message}",
        ]
        if part
    )
    system_prompt = CHAT_SYSTEM_PROMPT + _persona_block(state)
    own_session = http_session is None
    session = http_session or aiohttp.ClientSession()
    try:
        try:
            content, usage = await _ask_llm(
                session, config, system_prompt, user_message
            )
        except Exception as err:
            audit.record("chat.error", {"error": str(err)})
            state.sessions.append(conversation_id, "user", message)
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
            state.sessions.append(conversation_id, "user", message)
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
        state.sessions.append(conversation_id, "user", message)
        state.sessions.append(conversation_id, "assistant", say)
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
