"""Sensor platform for Pilot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PilotConfigEntry, PilotDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Pilot status sensor."""
    async_add_entities([PilotStatusSensor(entry.runtime_data, entry)])


class PilotStatusSensor(CoordinatorEntity[PilotDataUpdateCoordinator], SensorEntity):
    """Runtime status sensor with freshness/cost/queue attributes."""

    _attr_has_entity_name = True
    _attr_name = "Status"

    def __init__(
        self, coordinator: PilotDataUpdateCoordinator, entry: PilotConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Pilot",
            manufacturer="Pilot",
            model="Proactive home agent",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> str | None:
        """Return the runtime status (ok / degraded / down)."""
        return self.coordinator.data.get("status")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose snapshot details as attributes."""
        data = self.coordinator.data
        return {
            key: data.get(key)
            for key in ("vitrine_age_s", "cost_today", "queue_size", "runtime_version")
            if key in data
        }
