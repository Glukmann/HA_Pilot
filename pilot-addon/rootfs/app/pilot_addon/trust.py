"""Trust layer: confirmation queue, append-only audit log, whitelist, rollback.

Core safety invariants (archdoc §8):
- destructive/irreversible actions always require owner confirmation;
- every action is recorded in the audit log with cost and previous value;
- every applied change stores its previous value for one-click rollback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any
import uuid

logger = logging.getLogger("pilot.addon")

WHITELISTED_ACTIONS = {"setpoint_adjust"}  # ±3°C setpoint tweaks, archdoc §7


@dataclass
class QueueItem:
    """One yes/no decision awaiting the owner."""

    id: str
    title: str
    summary: str = ""
    action: dict[str, Any] = field(default_factory=dict)
    created_ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "created_ts": self.created_ts,
        }

    def to_record(self) -> dict[str, Any]:
        """Full persistence record (as_dict plus the action)."""
        return {**self.as_dict(), "action": self.action}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> QueueItem:
        action = record.get("action")
        return cls(
            id=str(record.get("id") or uuid.uuid4().hex[:12]),
            title=str(record.get("title") or "Предложение"),
            summary=str(record.get("summary") or ""),
            action=action if isinstance(action, dict) else {},
            created_ts=float(record.get("created_ts") or time.time()),
        )


class AuditLog:
    """Append-only JSONL audit trail: every action, every kopeck."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event: str,
        detail: dict[str, Any] | None = None,
        cost: float = 0.0,
    ) -> None:
        entry = {
            "ts": time.time(),
            "event": event,
            "detail": detail or {},
            "cost": cost,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class RollbackRegistry:
    """Previous values for applied changes — rollback is one write away."""

    def __init__(self) -> None:
        self._previous: dict[str, Any] = {}

    def remember(self, key: str, value: Any) -> None:
        self._previous[key] = value

    def previous(self, key: str) -> Any:
        return self._previous.get(key)


class TrustQueue:
    """The single confirmation queue for panel, chat, pushes and supervisor.

    When ``path`` is given, the queue survives restarts: every mutation is
    written atomically to a JSON file and restored on startup. An owner's
    pending decision must never die with a container restart.
    """

    def __init__(
        self,
        audit: AuditLog,
        rollback: RollbackRegistry,
        apply_action: Callable[[dict[str, Any]], None],
        whitelisted: Callable[[dict[str, Any]], bool] | None = None,
        path: Path | None = None,
    ) -> None:
        self.items: list[QueueItem] = []
        self._audit = audit
        self._rollback = rollback
        self._apply_action = apply_action
        self._whitelisted = whitelisted or (
            lambda action: action.get("type") in WHITELISTED_ACTIONS
        )
        self._path = path
        self.last_change_ts: float | None = None
        if path is not None:
            self._restore(path)
        # Learning stats: the raw material for persona adaptation (Phase 6).
        # Counts every decision path; by_kind breaks accept/reject down per
        # action kind (setpoint / proposal / domain of the target entity).
        self.stats: dict[str, int] = {
            "proposed": 0,
            "accepted": 0,
            "rejected": 0,
            "applied_silent": 0,
        }
        self.stats_by_kind: dict[str, dict[str, int]] = {}

    def as_learning(self) -> dict[str, Any]:
        """Learning stats snapshot for status/UI; copies, never references."""
        return {
            "total": dict(self.stats),
            "by_kind": {key: dict(val) for key, val in self.stats_by_kind.items()},
        }

    def reset_stats(self) -> None:
        """Clear learning stats (owner's Reset learning); queue is untouched."""
        for key in self.stats:
            self.stats[key] = 0
        self.stats_by_kind.clear()

    @staticmethod
    def _kind_key(action: dict[str, Any]) -> str:
        entity_id = str(action.get("entity_id") or "")
        if "." in entity_id:
            return entity_id.split(".", 1)[0]
        return str(action.get("kind") or action.get("type") or "other")

    def _count(self, counter: str, action: dict[str, Any]) -> None:
        self.stats[counter] = self.stats.get(counter, 0) + 1
        if counter in ("accepted", "rejected"):
            bucket = self.stats_by_kind.setdefault(
                self._kind_key(action), {"accepted": 0, "rejected": 0}
            )
            bucket[counter] += 1

    def _restore(self, path: Path) -> None:
        """Rebuild the queue from the persistence file (best effort)."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        seen: set[str] = set()
        for record in raw:
            if not isinstance(record, dict):
                continue
            item = QueueItem.from_record(record)
            if item.id in seen:
                continue
            seen.add(item.id)
            self.items.append(item)

    def _save(self) -> None:
        """Persist the queue atomically; failures are logged, never raised."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    [item.to_record() for item in self.items], ensure_ascii=False
                ),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError:
            logger.exception("queue persistence failed")

    def propose(self, title: str, action: dict[str, Any], summary: str = "") -> str:
        """Add a proposal; auto-apply if whitelisted, else queue for yes/no.

        Deduplicates: a proposal whose action is already queued returns the
        existing item's id instead of adding a copy — the daily supervisor
        must not re-propose a flag that is still awaiting the owner's answer.
        """
        item_id = uuid.uuid4().hex[:12]
        self.last_change_ts = time.time()
        if self._whitelisted(action):
            self._count("applied_silent", action)
            self._apply(action, confirmed_by="whitelist")
            return item_id
        for item in self.items:
            if item.action == action:
                return item.id
        item = QueueItem(id=item_id, title=title, summary=summary, action=action)
        self.items.append(item)
        self._count("proposed", action)
        self._save()
        self._audit.record("queue.proposed", {"id": item_id, "title": title})
        return item_id

    def confirm(self, item_id: str, decision: str) -> bool:
        """Resolve a queue item. Returns True if the item was found."""
        for item in list(self.items):
            if item.id != item_id:
                continue
            self.items.remove(item)
            self.last_change_ts = time.time()
            self._save()
            if decision == "yes":
                self._count("accepted", item.action)
                self._apply(item.action, confirmed_by="owner")
            else:
                self._count("rejected", item.action)
                self._audit.record(
                    "queue.rejected", {"id": item.id, "title": item.title}
                )
            return True
        return False

    def clear(self) -> None:
        """Drop every pending item (Reset all); persisted immediately."""
        self.items.clear()
        self.last_change_ts = time.time()
        self._save()

    def _apply(self, action: dict[str, Any], confirmed_by: str) -> None:
        key = str(action.get("rollback_key", action.get("type", "action")))
        self._rollback.remember(key, action.get("previous"))
        self._apply_action(action)
        self._audit.record(
            "action.applied",
            {"action": action, "confirmed_by": confirmed_by},
        )

    def rollback_last(self, key: str) -> bool:
        """Restore the previous value for a key, if recorded."""
        previous = self._rollback.previous(key)
        if previous is None:
            return False
        self._apply_action({"type": "rollback", "rollback_key": key, "value": previous})
        self._audit.record("action.rollback", {"key": key, "value": previous})
        return True
