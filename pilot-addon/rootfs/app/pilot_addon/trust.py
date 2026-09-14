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
from pathlib import Path
import time
from typing import Any
import uuid

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
    """The single confirmation queue for panel, chat, pushes and supervisor."""

    def __init__(
        self,
        audit: AuditLog,
        rollback: RollbackRegistry,
        apply_action: Callable[[dict[str, Any]], None],
        whitelisted: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.items: list[QueueItem] = []
        self._audit = audit
        self._rollback = rollback
        self._apply_action = apply_action
        self._whitelisted = whitelisted or (
            lambda action: action.get("type") in WHITELISTED_ACTIONS
        )

    def propose(self, title: str, action: dict[str, Any], summary: str = "") -> str:
        """Add a proposal; auto-apply if whitelisted, else queue for yes/no."""
        item_id = uuid.uuid4().hex[:12]
        if self._whitelisted(action):
            self._apply(action, confirmed_by="whitelist")
            return item_id
        item = QueueItem(id=item_id, title=title, summary=summary, action=action)
        self.items.append(item)
        self._audit.record("queue.proposed", {"id": item_id, "title": title})
        return item_id

    def confirm(self, item_id: str, decision: str) -> bool:
        """Resolve a queue item. Returns True if the item was found."""
        for item in list(self.items):
            if item.id != item_id:
                continue
            self.items.remove(item)
            if decision == "yes":
                self._apply(item.action, confirmed_by="owner")
            else:
                self._audit.record(
                    "queue.rejected", {"id": item.id, "title": item.title}
                )
            return True
        return False

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
