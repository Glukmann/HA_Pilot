"""Binary sensor platform for Pilot."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    PilotConfigEntry,
    PilotDataUpdateCoordinator,
)
from .entity import PilotEntity

VITRINE_MAX_AGE_S = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pilot binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PilotDataFreshSensor(coordinator, entry),
            PilotAwaitingConfirmationSensor(coordinator, entry),
        ]
    )


class PilotDataFreshSensor(PilotEntity, BinarySensorEntity):
    """Vitrine (home data snapshot) freshness: on = fresh, off = stale."""

    _attr_name = "Data fresh"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_data_fresh"

    @property
    def is_on(self) -> bool | None:
        """Fresh when runtime is reachable and the snapshot is recent."""
        if not self.coordinator.last_update_success:
            return False
        age = self.coordinator.data.get("vitrine_age_s")
        if age is None:
            return None
        return int(age) <= VITRINE_MAX_AGE_S


class PilotAwaitingConfirmationSensor(PilotEntity, BinarySensorEntity):
    """On while the trust loop waits for owner confirmation(s)."""

    _attr_name = "Awaiting confirmation"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_awaiting_confirmation"

    @property
    def is_on(self) -> bool | None:
        """True when the confirmation queue is not empty."""
        value = self.coordinator.data.get("awaiting_confirmation")
        if value is not None:
            return bool(value)
        queue = self.coordinator.data.get("queue_size")
        if queue is None:
            return None
        return int(queue) > 0
