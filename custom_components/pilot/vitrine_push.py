"""Vitrine pusher: the integration streams HA states into the runtime.

The integration runs inside Home Assistant, so no token is needed — this
keeps the add-on install fully UI-driven (Zigbee2MQTT-style UX). Push model
preserved: HA (master) pushes; the agent reads the vitrine, never polls.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant

from .coordinator import PilotConfigEntry

PUSH_DEBOUNCE_S = 2.0
IGNORED_DOMAINS = {"automation", "script", "zone"}


async def async_setup_vitrine_push(
    hass: HomeAssistant, entry: PilotConfigEntry
) -> None:
    """Forward all entity state changes to the Pilot runtime, debounced."""
    coordinator = entry.runtime_data
    pending: dict[str, dict[str, Any]] = {}
    task: asyncio.Task[None] | None = None

    async def _flush() -> None:
        if not pending:
            return
        batch = dict(pending)
        pending.clear()
        if not coordinator.last_update_success:
            return  # runtime down; soft degradation — skip this batch
        try:
            await coordinator.api.async_push_vitrine(batch)
        except Exception:
            coordinator.async_set_update_error(Exception("vitrine push failed"))

    async def _delayed_flush() -> None:
        await asyncio.sleep(PUSH_DEBOUNCE_S)
        await _flush()

    def _schedule_flush() -> None:
        nonlocal task

        def _create() -> None:
            nonlocal task
            if task is None or task.done():
                task = hass.loop.create_task(_delayed_flush())

        # State listeners can fire from a worker thread (HA schedules state
        # changes through executors); asyncio loop handles may only be touched
        # from the loop thread, so hop on via call_soon_threadsafe.
        hass.loop.call_soon_threadsafe(_create)

    def _on_state_change(event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is None or state.domain in IGNORED_DOMAINS:
            return
        pending[state.entity_id] = {
            "state": state.state,
            "attrs": dict(state.attributes),
            "last_changed": time.time(),
        }
        _schedule_flush()

    entry.async_on_unload(hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_change))
