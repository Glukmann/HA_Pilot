"""LLM supervisor: the daily adaptation run (docs/2026-09-13-supervisor-design.md).

The deterministic checker feeds state.flags; once a day this module asks an
LLM to reason over the flags + home snapshot and either applies whitelisted
setpoint tweaks (number./input_number. within ±3) or queues proposals for the
owner. Every run lands in the journal ($PILOT_DATA/supervisor-log.md) with
its token cost; the budget guard refuses to run past the daily limit.

Config lives in the supervisor section of $PILOT_DATA/pilot.json:

    "supervisor": {
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "model": "deepseek-chat",
      "max_tokens": 1500,
      "price_input_per_1m": 15.0,
      "price_output_per_1m": 60.0,
      "schedule": "07:00"
    }

base_url/api_key/model are required — without them the run is skipped
(audit: supervisor.skipped, reason no_config). The HA API base is
http://supervisor/core/api (Supervisor token from $SUPERVISOR_TOKEN);
override HA_API_BASE for standalone runs and tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import aiohttp

from .state import RuntimeState

logger = logging.getLogger("pilot.addon")

HA_API_BASE = os.environ.get("HA_API_BASE", "http://supervisor/core/api")
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_VITRINE_LINES = 120
JOURNAL_TAIL_LINES = 20
MAX_SETPOINT_DELTA = 3.0
FRESH_FLAGS_AGE_S = 12 * 3600
DEFAULT_SCHEDULE = "07:00"

Broadcast = Callable[[str, Any], Awaitable[None]]

SYSTEM_PROMPT = """Ты — супервизор умного дома (Pilot). Раз в день ты анализируешь \
флаги отклонений от детерминированного checker-слоя и решаешь, как подстроить \
политику дома.

Жёсткие правила:
- Молча (kind: "setpoint") разрешено менять ТОЛЬКО уставки (сущности number.* \
и input_number.*) и ТОЛЬКО в пределах ±3 от текущего значения.
- Всё остальное — только предложения (kind: "proposal") на подтверждение \
владельцу. Никаких действий с воротами, реле, выключателями, автоматизациями, \
камерами, медиа.
- Не чини железо: «сенсор мёртв», «устройство недоступно» — это предложение \
человеку, а не действие.
- Отвечай СТРОГО одним JSON-объектом, без пояснений вне JSON.

Формат ответа:
{"summary": "короткий вывод о состоянии дома",
 "decisions": [
   {"kind": "setpoint", "entity_id": "input_number.x", "value": 22, \
"reason": "почему"},
   {"kind": "proposal", "title": "короткий заголовок", \
"summary": "что изменится", "reason": "почему"}
]}

Поле decisions может быть пустым списком — если ничего делать не нужно."""


class SupervisorError(Exception):
    """A controlled failure of the run; journaled and audited by the caller."""


# -- config -------------------------------------------------------------------


def load_supervisor_config(state: RuntimeState) -> dict[str, Any] | None:
    """Return the supervisor config section, or None when unusable."""
    raw = state.read_config()
    if raw is None:
        return None
    section = raw.get("supervisor")
    if not isinstance(section, dict):
        return None
    missing = ("base_url", "api_key", "model")
    if not all(section.get(key) for key in missing):
        return None
    return section


def schedule_hhmm(state: RuntimeState) -> tuple[int, int]:
    """Configured daily run time "HH:MM" (local container time), default 07:00."""
    config = load_supervisor_config(state) or {}
    raw = str(config.get("schedule") or DEFAULT_SCHEDULE)
    try:
        hh_s, mm_s = raw.split(":", 1)
        hh, mm = int(hh_s), int(mm_s)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except ValueError:
        pass
    return 7, 0


def seconds_until(hh: int, mm: int, now: float | None = None) -> float:
    """Seconds of sleep until the next local HH:MM."""
    now = now or time.time()
    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    if target <= now:
        target += 24 * 3600
    return target - now


# -- journal ------------------------------------------------------------------


def _journal_path(state: RuntimeState) -> Path:
    return Path(state.data_dir) / "supervisor-log.md"


def _append_journal(state: RuntimeState, entry: str) -> None:
    path = _journal_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry.rstrip() + "\n\n")


def _journal_tail(state: RuntimeState) -> list[str]:
    try:
        text = _journal_path(state).read_text(encoding="utf-8")
    except OSError:
        return []
    return text.strip().splitlines()[-JOURNAL_TAIL_LINES:]


# -- prompt material ----------------------------------------------------------


def _setpoints(state: RuntimeState) -> dict[str, float]:
    """Current values of number./input_number. entities in the vitrine."""
    result: dict[str, float] = {}
    for eid, sample in state.vitrine.states.items():
        if not (eid.startswith("number.") or eid.startswith("input_number.")):
            continue
        if not isinstance(sample, dict):
            continue
        raw_value = sample.get("state")
        if raw_value is None:
            continue
        try:
            result[eid] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return result


def _build_user_message(state: RuntimeState, setpoints: dict[str, float]) -> str:
    lines = ["Флаги отклонений (checker):"]
    lines.extend(f"- {flag}" for flag in state.flags)
    lines.append("")
    lines.append("Карта дома (витрина):")
    lines.extend(state.vitrine.lines[-MAX_VITRINE_LINES:])
    lines.append("")
    lines.append("Текущие уставки (number/input_number):")
    lines.extend(f"- {eid}: {value:g}" for eid, value in sorted(setpoints.items()))
    journal_tail = _journal_tail(state)
    if journal_tail:
        lines.append("")
        lines.append("Хвост журнала супервизора:")
        lines.extend(journal_tail)
    return "\n".join(lines)


# -- LLM + HA calls -----------------------------------------------------------


async def _ask_llm(
    session: aiohttp.ClientSession, config: dict[str, Any], user_message: str
) -> tuple[str, dict[str, Any]]:
    """One chat/completions call; returns (content, usage)."""
    base_url = str(config["base_url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": int(config.get("max_tokens") or 1500),
    }
    async with session.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise SupervisorError(f"LLM HTTP {resp.status}")
        data = await resp.json()
    try:
        content = str(data["choices"][0]["message"]["content"])
        usage = dict(data.get("usage") or {})
    except (KeyError, IndexError, TypeError) as err:
        raise SupervisorError(f"LLM response shape: {err}") from err
    return content, usage


def _parse_decisions(content: str) -> tuple[str, list[dict[str, Any]], int]:
    """Strict-parse the LLM answer into (summary, decisions, dropped count).

    Raises ValueError when the envelope is unusable; individually invalid
    decisions are dropped and counted instead.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json")
        text = text.removeprefix("```")
        text = text.removesuffix("```").strip()
    raw = json.loads(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("summary"), str):
        raise ValueError("summary required")
    items = raw.get("decisions", [])
    if not isinstance(items, list):
        raise ValueError("decisions must be a list")
    decisions: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        kind = item.get("kind")
        reason = str(item.get("reason", ""))
        if kind == "setpoint":
            entity_id = item.get("entity_id")
            value = item.get("value")
            if (
                isinstance(entity_id, str)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                decisions.append(
                    {
                        "kind": "setpoint",
                        "entity_id": entity_id,
                        "value": value,
                        "reason": reason,
                    }
                )
            else:
                dropped += 1
        elif kind == "proposal":
            title = item.get("title")
            if isinstance(title, str) and title:
                decisions.append(
                    {
                        "kind": "proposal",
                        "title": title,
                        "summary": str(item.get("summary", "")),
                        "reason": reason,
                    }
                )
            else:
                dropped += 1
        else:
            dropped += 1
    return raw["summary"], decisions, dropped


async def _ha_set_value(
    session: aiohttp.ClientSession, entity_id: str, value: float
) -> None:
    """Apply a setpoint through the HA Core API (Supervisor proxy)."""
    domain = entity_id.split(".", 1)[0]
    url = f"{HA_API_BASE}/services/{domain}/set_value"
    headers = {"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}"}
    async with session.post(
        url,
        json={"entity_id": entity_id, "value": value},
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise SupervisorError(f"HA HTTP {resp.status}")


def _accrue_cost(
    state: RuntimeState, config: dict[str, Any], usage: dict[str, Any]
) -> tuple[int, int, float]:
    """Add the run's LLM cost to cost_today when prices are configured."""
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    cost = 0.0
    price_in = config.get("price_input_per_1m")
    price_out = config.get("price_output_per_1m")
    if isinstance(price_in, (int, float)) and isinstance(price_out, (int, float)):
        cost = (tokens_in * float(price_in) + tokens_out * float(price_out)) / 1_000_000
        state.cost_today += cost
    return tokens_in, tokens_out, cost


def _update_supervisor_status(state: RuntimeState, now: float, decisions: int) -> None:
    state.supervisor_status = {
        "last_run_ts": now,
        "last_decisions": decisions,
        "cost_today": round(state.cost_today, 4),
    }


# -- the run ------------------------------------------------------------------


async def run_supervisor(
    state: RuntimeState,
    *,
    http_session: aiohttp.ClientSession | None = None,
    broadcast: Broadcast | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """One daily supervisor run; never raises.

    Skips (audited as supervisor.skipped): no config, budget exhausted,
    checker silent or flags older than 12 h. An empty flag day journaled as
    "всё в норме" without calling the LLM (0 tokens).
    """
    now = now or time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    header = f"## {stamp} (Supervisor: дом)"
    audit = state.queue._audit
    state.reset_cost_if_new_day()

    async def _broadcast_status() -> None:
        if broadcast is not None:
            try:
                await broadcast("status", state.snapshot())
            except Exception:
                logger.exception("supervisor status broadcast failed")

    config = load_supervisor_config(state)
    if config is None:
        audit.record("supervisor.skipped", {"reason": "no_config"})
        _append_journal(
            state,
            f"{header}\nКонфигурация супервизора не задана (pilot.json → supervisor),"
            " запуск пропущен",
        )
        return {"skipped": "no_config"}

    if state.cost_today >= state.daily_budget:
        audit.record(
            "supervisor.skipped",
            {"reason": "budget", "cost_today": round(state.cost_today, 4)},
        )
        _append_journal(
            state,
            f"{header}\nДневной лимит исчерпан"
            f" (₽{state.cost_today:.2f} >= ₽{state.daily_budget:.2f}), запуск пропущен",
        )
        return {"skipped": "budget"}

    last_run = getattr(state.checker, "last_run_ts", None) if state.checker else None
    if not last_run or now - float(last_run) > FRESH_FLAGS_AGE_S:
        reason = "no_checker" if not last_run else "stale_flags"
        audit.record("supervisor.skipped", {"reason": reason})
        _append_journal(
            state,
            f"{header}\nФлаги протухли (checker молчит больше 12 ч), запуск пропущен",
        )
        return {"skipped": reason}

    if not state.flags:
        audit.record("supervisor.quiet", {})
        _append_journal(state, f"{header}\nВход: флагов нет\nИтог: всё в норме")
        _update_supervisor_status(state, now, 0)
        await _broadcast_status()
        return {"ok": True, "flags": 0, "decisions": 0}

    # LLM path: reason over flags + home snapshot.
    setpoints = _setpoints(state)
    message = _build_user_message(state, setpoints)
    own_session = http_session is None
    session = http_session or aiohttp.ClientSession()
    try:
        try:
            content, usage = await _ask_llm(session, config, message)
        except (SupervisorError, aiohttp.ClientError, TimeoutError) as err:
            audit.record("supervisor.error", {"error": str(err)})
            _append_journal(
                state,
                f"{header}\nВход: флаги [{', '.join(state.flags)}]\n"
                f"Ошибка LLM: {err}; ничего не исполнено",
            )
            await _broadcast_status()
            return {"error": str(err)}

        tokens_in, tokens_out, cost = _accrue_cost(state, config, usage)
        try:
            summary, decisions, dropped = _parse_decisions(content)
        except ValueError as err:
            audit.record("supervisor.parse_error", {"error": str(err)})
            _append_journal(
                state,
                f"{header}\nВход: флаги [{', '.join(state.flags)}]\n"
                f"LLM вернул невалидный JSON ({err}); ничего не исполнено",
            )
            _update_supervisor_status(state, now, 0)
            await _broadcast_status()
            return {"error": f"parse_error: {err}"}

        applied: list[str] = []
        proposed: list[str] = []
        for decision in decisions:
            if decision["kind"] == "setpoint":
                entity_id = decision["entity_id"]
                value = float(decision["value"])
                current = setpoints.get(entity_id)
                if current is None or abs(value - current) > MAX_SETPOINT_DELTA:
                    state.queue.propose(
                        title=f"Уставка {entity_id}: {_fmt(current)}→{_fmt(value)}",
                        summary=(
                            f"{decision['reason']} — вне вайтлиста ±3,"
                            " требуется подтверждение"
                        ),
                        action={
                            "type": "supervisor",
                            "kind": "setpoint",
                            "entity_id": entity_id,
                            "value": value,
                        },
                    )
                    proposed.append(entity_id)
                    continue
                try:
                    await _ha_set_value(session, entity_id, value)
                except (
                    SupervisorError,
                    aiohttp.ClientError,
                    TimeoutError,
                ) as err:
                    audit.record(
                        "supervisor.ha_error",
                        {"entity_id": entity_id, "error": str(err)},
                    )
                    state.queue.propose(
                        title=f"Уставка {entity_id}: {_fmt(current)}→{_fmt(value)}",
                        summary=(
                            f"{decision['reason']} — исполнение в HA не удалось: {err}"
                        ),
                        action={
                            "type": "supervisor",
                            "kind": "setpoint",
                            "entity_id": entity_id,
                            "value": value,
                        },
                    )
                    proposed.append(entity_id)
                    continue
                audit.record(
                    "supervisor.setpoint",
                    {"entity_id": entity_id, "old": current, "new": value},
                )
                applied.append(f"{entity_id} {_fmt(current)}→{_fmt(value)}")
            else:
                state.queue.propose(
                    title=decision["title"],
                    summary=decision["summary"] or decision["reason"],
                    action={
                        "type": "supervisor",
                        "kind": "proposal",
                        "title": decision["title"],
                    },
                )
                proposed.append(decision["title"])

        model = str(config["model"])
        usage_txt = f"Стоимость: {tokens_in} tok in / {tokens_out} tok out"
        if cost > 0:
            cost_line = f"{usage_txt} ≈ ₽{cost:.2f} ({model})"
        else:
            cost_line = f"{usage_txt} ({model})"
        entry = [
            header,
            f"Вход: флаги [{', '.join(state.flags)}]",
            f"Решение: {summary}",
        ]
        if applied:
            entry.append("Изменено: " + "; ".join(applied))
        if proposed:
            entry.append("На подтверждении: " + "; ".join(proposed))
        if dropped:
            entry.append(f"Отброшено невалидных решений: {dropped}")
        entry.append(cost_line)
        entry.append(
            f"Итог: {len(applied)} правок молча, {len(proposed)} на подтверждении"
        )
        _append_journal(state, "\n".join(entry))
        audit.record(
            "supervisor.run",
            {
                "flags": len(state.flags),
                "decisions": len(decisions),
                "applied": len(applied),
                "proposed": len(proposed),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": round(cost, 6),
            },
        )
        _update_supervisor_status(state, now, len(decisions))
        await _broadcast_status()
        return {
            "ok": True,
            "flags": len(state.flags),
            "applied": len(applied),
            "proposed": len(proposed),
            "cost": cost,
        }
    finally:
        if own_session:
            await session.close()


def _fmt(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"
