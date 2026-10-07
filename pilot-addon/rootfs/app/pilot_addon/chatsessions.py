"""Chat session memory: the conversation history per Assist conversation.

A session is a bounded list of turns (user/assistant text) keyed by the
Assist conversation_id, persisted under /data/chats/<id>.json so a
conversation survives restarts and add-on updates (see
docs/2026-10-07-openclaw-update-policy.md, data map).

Budget discipline: the cap is hard — old turns are dropped, not
summarized (summaries come later if the season of operation asks for it).
The history feeds the prompt as compact text, not as chat/completions
message pairs, to keep the system prompt authoritative.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import time
from typing import Any

logger = logging.getLogger("pilot.addon")

MAX_TURNS = 10
_SESSION_SLUG = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(conversation_id: str) -> str:
    slug = _SESSION_SLUG.sub("-", conversation_id).strip("-")
    return slug[:64] or "default"


class ChatSessions:
    """Persistent per-conversation turn lists."""

    def __init__(self, data_dir: Path, max_turns: int = MAX_TURNS) -> None:
        self._dir = data_dir / "chats"
        self._max_turns = max_turns

    def history(self, conversation_id: str) -> list[dict[str, Any]]:
        """The last turns of a session (each: {"role", "text", "ts"})."""
        try:
            raw = json.loads(self._path(conversation_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        turns = [
            item
            for item in raw
            if isinstance(item, dict)
            and item.get("role") in ("user", "assistant")
            and isinstance(item.get("text"), str)
        ]
        return turns[-self._max_turns :]

    def append(
        self, conversation_id: str, role: str, text: str, now: float | None = None
    ) -> None:
        """Add one turn and persist; failures are logged, never raised."""
        if not text.strip():
            return
        try:
            turns = self.history(conversation_id)
            turns.append({"role": role, "text": text.strip(), "ts": now or time.time()})
            turns = turns[-self._max_turns :]
            path = self._path(conversation_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.exception("chat session persistence failed")

    def _path(self, conversation_id: str) -> Path:
        return self._dir / f"{_slug(conversation_id)}.json"
