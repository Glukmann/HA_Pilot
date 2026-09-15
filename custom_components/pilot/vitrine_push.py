"""Vitrine pusher: the integration streams HA states into the runtime.

The integration runs inside Home Assistant, so no token is needed — this
keeps the add-on install fully UI-driven (Zigbee2MQTT-style UX). Push model
preserved: HA (master) pushes; the agent reads the vitrine, never polls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
import time
from typing import Any, cast

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

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

    def _attach_areas(batch: dict[str, dict[str, Any]]) -> None:
        """Annotate samples with their HA area (room) name.

        The agent reasons about the home room-by-room, so every entity
        carries its room. Entity area wins; an entity without one inherits
        its device's area.
        """
        ent_reg = er.async_get(hass)
        area_reg = ar.async_get(hass)
        dev_reg = dr.async_get(hass)
        area_names = {area.id: area.name for area in area_reg.areas.values()}
        for eid, sample in batch.items():
            entry = ent_reg.entities.get(eid)
            area_id = entry.area_id if entry else None
            if area_id is None and entry is not None and entry.device_id is not None:
                devices = dev_reg.devices
                if isinstance(devices, Mapping):  # HA <= 2026.8: dict-like registry
                    device = devices.get(entry.device_id)
                else:  # HA >= 2026.9: Collection[DeviceEntry]
                    entries = cast(Collection[dr.DeviceEntry], devices)
                    device = next((d for d in entries if d.id == entry.device_id), None)
                area_id = device.area_id if device else None
            name = area_names.get(area_id) if area_id else None
            if name:
                sample["area"] = name

    async def _flush() -> None:
        if not pending:
            return
        batch = dict(pending)
        pending.clear()
        if not coordinator.last_update_success:
            return  # runtime down; soft degradation — skip this batch
        _attach_areas(batch)
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

    def _cancel_pending_flush() -> None:
        # The debounce task survives entry unload unless cancelled; pytest's
        # hass fixture unloads entries before checking for lingering tasks.
        nonlocal task
        if task is not None and not task.done():
            task.cancel()

    entry.async_on_unload(_cancel_pending_flush)

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
