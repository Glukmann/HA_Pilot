"""Text platform for the agent's current focus."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    """Set up Pilot text entities."""
    async_add_entities([CurrentFocusText(entry.runtime_data, entry)])


class CurrentFocusText(PilotEntity, TextEntity):
    """What the agent currently considers most important (editable)."""

    _attr_name = "Current focus"
    _attr_native_max = 500

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_focus"

    @property
    def native_value(self) -> str:
        """Return the current focus from runtime data or cache."""
        value = self.coordinator.persona_value("current_focus")
        return str(value) if value else ""

    async def async_set_value(self, value: str) -> None:
        """Write a new focus note to the runtime."""
        await self.coordinator.async_set_policy_value("current_focus", value)
        self.async_write_ha_state()
