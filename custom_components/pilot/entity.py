"""Shared entity base for Pilot."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PilotConfigEntry, PilotDataUpdateCoordinator


class PilotEntity(CoordinatorEntity[PilotDataUpdateCoordinator]):
    """Base class: links every Pilot entity to the single 'Pilot' device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: PilotDataUpdateCoordinator, entry: PilotConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Pilot",
            manufacturer="Pilot",
            model="Proactive home agent",
            entry_type=DeviceEntryType.SERVICE,
        )
