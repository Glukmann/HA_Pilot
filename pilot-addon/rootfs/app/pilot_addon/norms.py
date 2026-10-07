"""Norm detector: habits learned from the event journal, 0 tokens.

A norm is a stable recurring transition: e.g. «вытяжка включается около
7:00, 6 из 7 дней». Candidates become trust-queue proposals the owner
accepts or declines (docs/2026-10-07-norms-and-habits-design.md).

Detection is purely deterministic: trailing window of events, minimum
occurrences on distinct days, bounded circular clock spread. Guards:
learning pauses outside the normal home mode, two declines on «pattern»
proposals silence the category, at most one norm proposal per day.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import json
import logging
from pathlib import Path
import time
from typing import Any

logger = logging.getLogger("pilot.addon")

WINDOW_DAYS = 14
RECENT_DAYS = 7
MIN_EVENTS = 5
MIN_DAYS = 4
MAX_SPREAD_S = 40 * 60
NORM_DOMAINS = ("light", "switch", "fan", "climate")
KIND_STATES = {"on": "schedule_on", "off": "schedule_off"}
CATEGORY = "pattern"

DAY_S = 24 * 3600
_CSV_PATH = Path(__file__).parent / "locales" / "norms.csv"
_STATE_PATH_NAME = "norms.json"


@lru_cache(maxsize=1)
def _norm_rows() -> dict[str, dict[str, str]]:
    with _CSV_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return {
            (row.get("key") or "").strip(): {
                lang: (text or "")
                for lang, text in row.items()
                if lang and lang != "key"
            }
            for row in reader
            if (row.get("key") or "").strip()
        }


def _norm_text(key: str, language: str) -> str:
    return (
        _norm_rows()
        .get(key, {})
        .get(language, _norm_rows().get(key, {}).get("ru", key))
    )


def _local_minute(ts: float) -> int:
    lt = time.localtime(ts)
    return lt.tm_hour * 60 + lt.tm_min


def _circular_spread_s(minutes: list[int]) -> int:
    """Largest gap on the clock face subtracted from 24h — midnight-safe."""
    ordered = sorted(set(minutes))
    if len(ordered) == 1:
        return 0
    gaps = [
        (ordered[(i + 1) % len(ordered)] - ordered[i]) % (24 * 60)
        for i in range(len(ordered))
    ]
    return (24 * 60 - max(gaps)) * 60


def detect_norms(state: Any, now: float | None = None) -> list[dict[str, Any]]:
    """All stable on/off norms over the trailing window (pure read)."""
    now = now or time.time()
    events = state.events.transitions(now - WINDOW_DAYS * DAY_S)
    by_target: dict[tuple[str, str], list[float]] = {}
    for event in events:
        eid = event["eid"]
        domain = eid.split(".", 1)[0]
        if domain not in NORM_DOMAINS:
            continue
        target = KIND_STATES.get(event["state"])
        if target is None:
            continue
        by_target.setdefault((eid, target), []).append(event["ts"])

    candidates: list[dict[str, Any]] = []
    for (eid, target), timestamps in sorted(by_target.items()):
        recent = [ts for ts in timestamps if ts >= now - RECENT_DAYS * DAY_S]
        days = {time.strftime("%Y-%m-%d", time.localtime(ts)) for ts in recent}
        if len(recent) < MIN_EVENTS or len(days) < MIN_DAYS:
            continue
        spread = _circular_spread_s([_local_minute(ts) for ts in recent])
        if spread > MAX_SPREAD_S:
            continue
        median = sorted(_local_minute(ts) for ts in recent)[len(recent) // 2]
        candidates.append(
            {
                "entity_id": eid,
                "kind": target,
                "time": f"{median // 60:02d}:{median % 60:02d}",
                "days": len(days),
                "window": RECENT_DAYS,
                "occurrences": len(recent),
            }
        )
    return candidates


def _state_path(state: Any) -> Path:
    return Path(state.data_dir) / _STATE_PATH_NAME


def _load_meta(state: Any) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_meta(state: Any, meta: dict[str, Any]) -> None:
    path = _state_path(state)
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.exception("norm meta persistence failed")


# -- decision memory: what the owner already decided ------------------------------


def _signature(action: dict[str, Any]) -> str:
    """The owner's decision key: one concrete habit or deviation."""
    entity_id = str(action.get("entity_id") or "")
    kind = str(action.get("kind") or action.get("service") or "")
    return f"{entity_id}:{kind}"


def record_decision(state: Any, action: dict[str, Any], decision: str) -> None:
    """Persist an accepted/declined norm or deviation — never re-ask it.

    This is the accumulated knowledge about the owner: «выключение света
    в коридоре автоматизировать не нужно» должно помниться дольще, чем
    живёт статистика очереди.
    """
    if action.get("type") not in ("pattern", "deviation"):
        return
    meta = _load_meta(state)
    bucket = "accepted" if decision == "yes" else "declined"
    decided = meta.setdefault("decided", {"accepted": [], "declined": []})
    signature = _signature(action)
    if signature in decided["accepted"] or signature in decided["declined"]:
        return
    decided[bucket] = (decided[bucket] + [signature])[-200:]
    _save_meta(state, meta)


def _decided(state: Any, bucket: str) -> set[str]:
    decided = _load_meta(state).get("decided")
    if not isinstance(decided, dict):
        return set()
    values = decided.get(bucket)
    return set(values) if isinstance(values, list) else set()


def _declined_norm_titles(state: Any) -> set[str]:
    """Titles of our norm proposals that the owner rejected (audit scan)."""
    path = state.queue._audit.path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-300:]
    except OSError:
        return set()
    declined: set[str] = set()
    known = set(_load_meta(state).get("proposed_titles") or [])
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") != "queue.rejected":
            continue
        detail = record.get("detail")
        if isinstance(detail, dict) and detail.get("title") in known:
            declined.add(str(detail["title"]))
    return declined


def propose_norms(
    state: Any, now: float | None = None, language: str = "ru"
) -> list[str]:
    """Turn candidates into queue proposals, under the habit guards.

    Returns the ids of newly proposed items (empty when everything is
    silent: wrong mode, declined before, or the daily cap is spent).
    """
    now = now or time.time()
    if state.mode != "normal":
        return []
    if len(_declined_norm_titles(state)) >= 2:
        return []
    meta = _load_meta(state)
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    if meta.get("last_proposal_day") == today:
        return []

    proposed: list[str] = []
    decided = _decided(state, "accepted") | _decided(state, "declined")
    for candidate in detect_norms(state, now):
        eid = candidate["entity_id"]
        if f"{eid}:{candidate['kind']}" in decided:
            continue  # хозяин уже решал эту автоматизацию — не предлагать повторно
        sample = state.vitrine.states.get(eid)
        attrs = sample.get("attrs") if isinstance(sample, dict) else None
        attrs = attrs if isinstance(attrs, dict) else {}
        name = str(attrs.get("friendly_name") or eid)
        kind_key = (
            "norm.propose.on"
            if candidate["kind"] == "schedule_on"
            else "norm.propose.off"
        )
        title = _norm_text(kind_key, language).format(
            name=name,
            time=candidate["time"],
            days=candidate["days"],
            window=candidate["window"],
        )
        action = {
            "type": CATEGORY,
            "kind": candidate["kind"],
            "entity_id": eid,
            "time": candidate["time"],
        }
        # Dedup: an unanswered identical norm stays one queue item.
        if any(item.action == action for item in state.queue.items):
            continue
        item_id = state.queue.propose(
            title=title,
            summary=(
                f"{candidate['occurrences']} включений/выключений за "
                f"{candidate['window']} дней, разброс ±20 мин"
            ),
            action=action,
        )
        proposed.append(item_id)
        meta["last_proposal_day"] = today
        meta.setdefault("proposed_titles", [])
        meta["proposed_titles"] = (meta["proposed_titles"] + [title])[-20:]
        _save_meta(state, meta)
        logger.info("norm proposed: %s", title)
        break  # лимитер назойливости: одна норма в день
    return proposed


# -- deviations ---------------------------------------------------------------------


def _entity_name(state: Any, eid: str) -> str:
    sample = state.vitrine.states.get(eid)
    attrs = sample.get("attrs") if isinstance(sample, dict) else None
    attrs = attrs if isinstance(attrs, dict) else {}
    return str(attrs.get("friendly_name") or eid)


# Flag prefix -> (template key, neutralising service call).
_DEVIATION_RULES: dict[str, tuple[str, str, str]] = {
    # flag prefix: (locales key, service, data)
    "light_always_on": ("deviation.light", "turn_off", ""),
    "gate_stuck": ("deviation.gate", "turn_off", ""),
}


def propose_deviations(
    state: Any, now: float | None = None, language: str = "ru"
) -> list[str]:
    """Turn checker deviation flags into 'neutralise it?' proposals.

    Same trust discipline as norms: proposals only, dedup while
    unanswered, at most one deviation proposal per day, silent outside
    the normal mode. Accepted 'yes' executes the neutralising call —
    through the executor, which re-checks the reversible whitelist.
    """
    now = now or time.time()
    if state.mode != "normal":
        return []
    meta = _load_meta(state)
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    if meta.get("last_deviation_day") == today:
        return []

    decided = _decided(state, "accepted") | _decided(state, "declined")
    for flag in state.flags:
        prefix, _, eid = flag.partition(":")
        rule = _DEVIATION_RULES.get(prefix)
        if rule is None or not eid:
            continue
        key, service, _ = rule
        if f"{eid}:{service}" in decided:
            continue  # хозяин уже отвечал на это отклонение — молчим
        action = {
            "type": "deviation",
            "domain": eid.split(".", 1)[0],
            "service": service,
            "entity_id": eid,
            "data": {},
        }
        if any(item.action == action for item in state.queue.items):
            continue
        title = _norm_text(key, language).format(name=_entity_name(state, eid))
        item_id = state.queue.propose(
            title=title,
            summary="Отклонение от обычного поведения (checker-слой)",
            action=action,
        )
        meta["last_deviation_day"] = today
        _save_meta(state, meta)
        logger.info("deviation proposed: %s", title)
        return [item_id]
    return []
