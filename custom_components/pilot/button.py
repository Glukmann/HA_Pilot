"""Button platform for Pilot resets."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up Pilot buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ResetLearningButton(coordinator, entry),
            ResetAllButton(coordinator, entry),
        ]
    )


class ResetLearningButton(PilotEntity, ButtonEntity):
    """Reset persona learning (accepted/rejected stats, adaptation journal)."""

    _attr_name = "Reset learning"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reset_learning"

    async def async_press(self) -> None:
        """Ask the runtime to drop learned persona state."""
        await self.coordinator.api.async_reset("learning")


class ResetAllButton(PilotEntity, ButtonEntity):
    """Full reset: memory levels 2-5, queue, learning."""

    _attr_name = "Reset all"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reset_all"

    async def async_press(self) -> None:
        """Ask the runtime for a full reset."""
        await self.coordinator.api.async_reset("all")
