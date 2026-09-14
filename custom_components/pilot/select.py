"""Select platform for Pilot persona presets and home modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    PERSONA_PRESETS,
    PILOT_MODES,
    PilotConfigEntry,
    PilotDataUpdateCoordinator,
)
from .entity import PilotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pilot selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PersonaPresetSelect(coordinator, entry),
            PilotModeSelect(coordinator, entry),
        ]
    )


class PersonaPresetSelect(PilotEntity, SelectEntity):
    """Named persona presets = ready-made slider combinations."""

    _attr_name = "Persona preset"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_options = list(PERSONA_PRESETS)
        self._attr_unique_id = f"{entry.entry_id}_persona_preset"

    @property
    def current_option(self) -> str | None:
        """Return the active preset from runtime data or cache."""
        value = self.coordinator.persona_value("persona_preset")
        return str(value) if value in PERSONA_PRESETS else None

    async def async_select_option(self, option: str) -> None:
        """Apply a preset (options are preset keys)."""
        if option not in PERSONA_PRESETS:
            raise ValueError(f"Unknown preset: {option}")
        await self.coordinator.async_set_policy_value("persona_preset", option)
        self.async_write_ha_state()


class PilotModeSelect(PilotEntity, SelectEntity):
    """Home mode: normal / vacation / guests / sick."""

    _attr_name = "Home mode"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_options = list(PILOT_MODES)
        self._attr_unique_id = f"{entry.entry_id}_mode"

    @property
    def current_option(self) -> str | None:
        """Return the active mode from runtime data or cache."""
        value = self.coordinator.persona_value("mode")
        return str(value) if value in PILOT_MODES else None

    async def async_select_option(self, option: str) -> None:
        """Set a home mode (options are mode keys)."""
        if option not in PILOT_MODES:
            raise ValueError(f"Unknown mode: {option}")
        await self.coordinator.async_set_policy_value("mode", option)
        self.async_write_ha_state()
