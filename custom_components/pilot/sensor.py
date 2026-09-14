"""Sensor platform for Pilot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    PilotConfigEntry,
    PilotDataUpdateCoordinator,
)
from .entity import PilotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pilot sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PilotStatusSensor(coordinator, entry),
            PilotCostTodaySensor(coordinator, entry),
            PilotSuggestionsSensor(coordinator, entry),
        ]
    )


class PilotStatusSensor(PilotEntity, SensorEntity):
    """Runtime status (ok / degraded / down) with snapshot attributes."""

    _attr_name = "Status"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str | None:
        """Return the runtime status."""
        value = self.coordinator.data.get("status")
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose snapshot details as attributes."""
        data = self.coordinator.data
        return {
            key: data.get(key)
            for key in (
                "vitrine_age_s",
                "queue_size",
                "runtime_version",
            )
            if key in data
        }


class PilotCostTodaySensor(PilotEntity, SensorEntity):
    """LLM spend today in currency units (budget guard)."""

    _attr_name = "Cost today"
    _attr_native_unit_of_measurement = "₽"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cost_today"

    @property
    def native_value(self) -> float | None:
        """Return today's LLM cost."""
        value = self.coordinator.data.get("cost_today")
        return float(value) if value is not None else None


class PilotSuggestionsSensor(PilotEntity, SensorEntity):
    """Number of suggestions waiting in the confirmation queue."""

    _attr_name = "Pending suggestions"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_suggestions"

    @property
    def native_value(self) -> int | None:
        """Return the confirmation queue size."""
        value = self.coordinator.data.get("queue_size")
        return int(value) if value is not None else None
