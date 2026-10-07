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

from .director import direct_answer
from .i18n import (
    CHAT_SYSTEM_PROMPTS,
    MODE_NAMES,
    OWNER_MEMORY_TITLES,
    PERSONA_BLOCK_FOCUS,
    PERSONA_BLOCK_SLIDERS,
    PERSONA_BLOCK_TITLES,
    PRESET_NAMES,
    PROMPT_HEADER_TRUNC,
    PROMPT_HEADER_VITRINE,
    PROMPT_HISTORY_TITLE,
    PROMPT_OWNER_MESSAGE,
    PROMPT_ROLES,
    VERBOSITY_STYLES,
    VERBOSITY_STYLES_LONG,
    resolve_language,
)
from .safety import classify
from .state import RuntimeState
from .supervisor import _accrue_cost, _ask_llm, load_supervisor_config

logger = logging.getLogger("pilot.addon")

MAX_VITRINE_LINES = 300


def _persona_block(state: RuntimeState, language: str) -> str:
    """Persona/mode/focus appended to the system prompt — the voice tuning."""
    verbosity = state.persona.get("verbosity", 50)
    style = VERBOSITY_STYLES_LONG[language]
    for limit, candidate in VERBOSITY_STYLES[language]:
        if verbosity < limit:
            style = candidate
            break
    titles = PERSONA_BLOCK_TITLES[language]
    sliders = PERSONA_BLOCK_SLIDERS[language]
    preset = PRESET_NAMES[language].get(state.persona_preset, state.persona_preset)
    lines = [
        "",
        titles[0],
        (
            f"{titles[1]} «{preset}» "
            f"({sliders[0]} {state.persona.get('butler_observer', 50)}/100, "
            f"{sliders[1]} {state.persona.get('politeness', 50)}/100, "
            f"{sliders[2]} {state.persona.get('conservative', 50)}/100)."
        ),
        f"{titles[2]} {MODE_NAMES[language].get(state.mode, state.mode)}.",
        style,
    ]
    if state.current_focus.strip():
        lines.append(f"{PERSONA_BLOCK_FOCUS[language]} {state.current_focus.strip()}.")
    return "\n".join(lines)


def _history_block(state: RuntimeState, conversation_id: str, language: str) -> str:
    """Render the recent turns of this conversation for the prompt."""
    turns = state.sessions.history(conversation_id)
    if not turns:
        return ""
    who = PROMPT_ROLES[language]
    lines = [
        PROMPT_HISTORY_TITLE[language],
        *[f"{who[t['role']]}: {t['text']}" for t in turns],
    ]
    return "\n".join(lines)


_OWNER_MEMORY_REJECTS = 8
_OWNER_MEMORY_AUDIT_TAIL = 300


def _owner_memory_block(state: RuntimeState, language: str) -> str:
    """Learning stats + recent rejections — the owner's decision history."""
    labels = OWNER_MEMORY_TITLES[language]
    learning = state.queue.as_learning()
    total = learning["total"]
    lines = [
        labels["title"],
        labels["total"].format(
            proposed=total["proposed"],
            accepted=total["accepted"],
            rejected=total["rejected"],
            silent=total["applied_silent"],
        ),
    ]
    kinds = sorted(
        learning["by_kind"].items(),
        key=lambda kv: kv[1]["accepted"] + kv[1]["rejected"],
        reverse=True,
    )[:5]
    if kinds:
        detail = "; ".join(
            labels["kinds_pair"].format(kind=kind, **v) for kind, v in kinds
        )
        lines.append(f"{labels['kinds_prefix']}{detail}.")
    rejections = _recent_rejection_titles(state)
    if rejections:
        lines.append(labels["rejects_title"])
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
    language: str | None = None,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Answer one chat message; never raises, always something to say."""
    state.reset_cost_if_new_day()
    audit = state.queue._audit
    base: dict[str, Any] = {"say": "", "actions": []}
    conversation_id = conversation_id or "default"
    lang = resolve_language(state, language)

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

    # Deterministic fast path: simple asks never reach the LLM (0 tokens).
    direct = direct_answer(state, message, lang)
    if direct is not None:
        audit.record(
            "chat.fallback",
            {"actions": len(direct["actions"]), "say": direct["say"]},
        )
        state.sessions.append(conversation_id, "user", message)
        state.sessions.append(conversation_id, "assistant", direct["say"])
        return direct

    all_lines = state.vitrine.lines
    lines = all_lines[-MAX_VITRINE_LINES:]
    header = PROMPT_HEADER_VITRINE[lang]
    if len(lines) < len(all_lines):
        header += PROMPT_HEADER_TRUNC[lang].format(
            shown=len(lines), total=len(all_lines)
        )
    user_message = "\n".join(
        part
        for part in [
            header,
            *lines,
            "",
            _owner_memory_block(state, lang),
            _history_block(state, conversation_id, lang),
            f"{PROMPT_OWNER_MESSAGE[lang]} {message}",
        ]
        if part
    )
    system_prompt = CHAT_SYSTEM_PROMPTS[lang] + _persona_block(state, lang)
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
