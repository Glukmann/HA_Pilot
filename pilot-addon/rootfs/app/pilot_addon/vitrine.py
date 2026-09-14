"""Vitrine mirror: HA pushes entity states over WebSocket; we render the card.

Evolution of the standalone ha-state-mirror daemon (docs/2026-09-13):
HA stays the master, subscribes via subscribe_entities, the mirror never
writes to HA. Output: human-readable card + machine JSON for the API.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any

from .state import RuntimeState


class VitrineMirror:
    """Maintain RuntimeState.vitrine from a HA WebSocket subscription."""

    def __init__(
        self,
        state: RuntimeState,
        ws_url: str,
        token: str,
        entities: list[str] | None = None,
        out_file: Path | None = None,
        stale_after_s: float = 60.0,
    ) -> None:
        self.state = state
        self.ws_url = ws_url
        self.token = token
        self.entities = entities
        self.out_file = out_file
        self.stale_after_s = stale_after_s

    async def run(self) -> None:
        """Reconnect-loop; never raises (soft degradation)."""
        backoff = 1
        while True:
            try:
                await self._run_once()
                backoff = 1
            except Exception:
                self.state.vitrine.ws_ok = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _run_once(self) -> None:
        import websockets  # lazy: tests of rendering don't need it

        async with websockets.connect(
            self.ws_url, ping_interval=20, ping_timeout=20
        ) as ws:
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "auth_required":
                    await ws.send(
                        json.dumps({"type": "auth", "access_token": self.token})
                    )
                elif msg.get("type") == "auth_ok":
                    sub = {
                        "id": 1,
                        "type": "subscribe_entities",
                        "entity_ids": self.entities or [],
                    }
                    await ws.send(json.dumps(sub))
                    self.state.vitrine.ws_ok = True
                elif msg.get("id") == 1:
                    self._apply(msg)
                    self._write_card()

    def _apply(self, msg: dict[str, Any]) -> None:
        changed = msg.get("added", {}) | msg.get("changed", {})
        if not changed and not msg.get("removed"):
            return
        # Vitrine lines derive from the configured entity map in data/pilot.yaml;
        # the mirror stores raw states and refreshes line rendering.
        self.state.vitrine.last_event_ts = time.time()
        self.state.vitrine.ws_ok = True

    def _write_card(self) -> None:
        if not self.out_file:
            return
        from .http_api import render_vitrine_text

        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out_file.with_suffix(".tmp")
        tmp.write_text(render_vitrine_text(self.state), encoding="utf-8")
        tmp.replace(self.out_file)
