"""Event journal: the append-only history behind norm learning.

The vitrine holds the CURRENT state; habits live in transitions. Every
real state change pushed by the HA integration is recorded here as one
JSON line {ts, eid, state}, rotated after ``max_age_days``. This is the
deterministic fuel for the norm detector (docs/2026-10-07-norms-and-
habits-design.md) — 0 tokens, local, tiny.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any

logger = logging.getLogger("pilot.addon")

DAY_S = 24 * 3600


class EventLog:
    """Append-only entity state transitions with age-based rotation."""

    def __init__(self, path: Path, max_age_days: int = 30) -> None:
        self.path = path
        self.max_age_days = max_age_days
        self._last_rotate_day = time.strftime("%Y-%m-%d", time.localtime())

    def record(self, eid: str, state: str, ts: float | None = None) -> None:
        """Append one transition; rotation fires at most once a day."""
        ts = ts or time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"ts": ts, "eid": eid, "state": state}, ensure_ascii=False
                    )
                    + "\n"
                )
        except OSError:
            logger.exception("event journal write failed")
            return
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day != self._last_rotate_day:
            self.rotate(ts)
            self._last_rotate_day = day

    def rotate(self, now: float | None = None) -> int:
        """Drop records older than max_age_days; returns kept count."""
        now = now or time.time()
        cutoff = now - self.max_age_days * DAY_S
        kept: list[str] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(record.get("ts") or 0) >= cutoff:
                kept.append(line)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            logger.exception("event journal rotation failed")
        return len(kept)

    def transitions(self, since: float) -> list[dict[str, Any]]:
        """All records with ts >= since (newest last), bad lines skipped."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            try:
                raw_ts = record.get("ts")
                ts = float(raw_ts) if raw_ts is not None else -1.0
            except (TypeError, ValueError):
                continue
            if ts >= since:
                records.append(
                    {
                        "ts": ts,
                        "eid": str(record.get("eid")),
                        "state": str(record.get("state")),
                    }
                )
        records.sort(key=lambda r: r["ts"])
        return records
