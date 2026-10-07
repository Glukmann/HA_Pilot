"""Weekly reflexion: one LLM run turning statistics into observations.

The digest reads the same deterministic fuel as the norm detector —
detected norms, trailing-week event activity, the owner's decisions — and
writes a short human-readable entry to $PILOT_DATA/reflexion-log.md.
Hard cadence: at most one run per ISO week, budget-guarded like the
supervisor, silent outside the normal home mode. This is the only place
habit learning spends tokens on prose (docs/2026-10-07-norms-and-habits-
design.md, layer 4).
"""

from __future__ import annotations

from collections import Counter
import logging
from pathlib import Path
import time
from typing import Any

import aiohttp

from .norms import _load_meta, _norm_text, _save_meta, detect_norms
from .supervisor import _accrue_cost, _ask_llm, load_supervisor_config

logger = logging.getLogger("pilot.addon")

DAY_S = 24 * 3600
RECENT_DAYS = 7
TOP_ENTITIES = 10


def _week_key(now: float) -> str:
    return time.strftime("%G-%W", time.localtime(now))


def _journal_path(state: Any) -> Path:
    return Path(state.data_dir) / "reflexion-log.md"


def _activity_lines(state: Any, now: float) -> list[str]:
    """Top entities by transition count over the trailing week."""
    events = state.events.transitions(now - RECENT_DAYS * DAY_S)
    counts: Counter[str] = Counter(event["eid"] for event in events)
    lines = [
        f"- {eid}: {count} переходов" for eid, count in counts.most_common(TOP_ENTITIES)
    ]
    lines.append(f"- Всего переходов за неделю: {len(events)}")
    return lines


def _decision_lines(state: Any) -> list[str]:
    learning = state.queue.as_learning()
    total = learning["total"]
    lines = [
        (
            f"- Решения хозяина: предложено {total['proposed']}, "
            f"принято {total['accepted']}, отклонено {total['rejected']}"
        )
    ]
    decided = _load_meta(state).get("decided")
    if isinstance(decided, dict):
        lines.append(
            f"- Автоматизации: принято {len(decided.get('accepted') or [])}, "
            f"отклонено {len(decided.get('declined') or [])}"
        )
    return lines


def _build_user_message(state: Any, now: float) -> str:
    norms = detect_norms(state, now)
    lines = ["Выученные нормы (устойчивые расписания):"]
    if norms:
        lines.extend(
            f"- {n['entity_id']}: {n['kind']} около {n['time']} "
            f"({n['days']} из {n['window']} дней)"
            for n in norms
        )
    else:
        lines.append("- пока нет")
    lines.append("")
    lines.append("Активность за неделю:")
    lines.extend(_activity_lines(state, now))
    lines.append("")
    lines.append("Решения:")
    lines.extend(_decision_lines(state))
    return "\n".join(lines)


async def run_reflexion(
    state: Any,
    *,
    http_session: aiohttp.ClientSession | None = None,
    now: float | None = None,
    language: str = "ru",
) -> dict[str, Any]:
    """One weekly reflective run; self-gating, never raises."""
    now = now or time.time()
    audit = state.queue._audit
    week = _week_key(now)
    meta = _load_meta(state)
    if meta.get("last_reflexion_week") == week:
        return {"skipped": "done"}
    if state.mode != "normal":
        audit.record("reflexion.skipped", {"reason": "mode"})
        return {"skipped": "mode"}

    state.reset_cost_if_new_day()
    config = load_supervisor_config(state)
    if config is None:
        audit.record("reflexion.skipped", {"reason": "no_config"})
        return {"skipped": "no_config"}
    if state.cost_today >= state.daily_budget:
        audit.record("reflexion.skipped", {"reason": "budget"})
        return {"skipped": "budget"}

    lang = language if language in ("ru", "en") else "ru"
    system_prompt = _norm_text("reflexion.system", lang)
    user_message = _build_user_message(state, now)

    own_session = http_session is None
    session = http_session or aiohttp.ClientSession()
    try:
        try:
            content, usage = await _ask_llm(
                session, config, system_prompt, user_message
            )
        except Exception as err:
            audit.record("reflexion.error", {"error": str(err)})
            return {"error": "llm"}
        tokens_in, tokens_out, cost = _accrue_cost(state, config, usage)
        entry = _norm_text("reflexion.header", lang).format(
            week=week, content=content.strip()
        )
        path = _journal_path(state)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(entry.rstrip() + "\n\n")
        except OSError:
            logger.exception("reflexion journal write failed")
        meta["last_reflexion_week"] = week
        _save_meta(state, meta)
        audit.record(
            "reflexion.run",
            {
                "week": week,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": round(cost, 6),
            },
        )
        return {"ok": True, "week": week, "cost": round(cost, 6)}
    finally:
        if own_session:
            await session.close()
