"""Number platform for Pilot persona sliders and the daily budget."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    PERSONA_SLIDERS,
    PilotConfigEntry,
    PilotDataUpdateCoordinator,
)
from .entity import PilotEntity

SLIDER_DEFINITIONS: dict[str, tuple[str, str]] = {
    "butler_observer": (
        "Persona: butler ↔ observer",
        "How often Pilot proposes vs quietly journals",
    ),
    "politeness": ("Persona: asks ↔ decides", "Width of the auto-apply whitelist"),
    "verbosity": ("Persona: dry ↔ chatty", "Reply length and explanations"),
    "conservative": (
        "Persona: conservative ↔ experimenter",
        "How often to suggest novel ideas",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pilot numbers."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        PersonaSlider(coordinator, entry, slider) for slider in PERSONA_SLIDERS
    ]
    entities.append(DailyBudgetNumber(coordinator, entry))
    async_add_entities(entities)


class PersonaSlider(PilotEntity, NumberEntity):
    """One persona scale (0-100), mirrored to the runtime and cached."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
        slider: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._slider = slider
        name, description = SLIDER_DEFINITIONS[slider]
        self._attr_name = name
        self._attr_entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_persona_{slider}"

    @property
    def native_value(self) -> float:
        """Return the slider value from runtime data or cache."""
        persona = self.coordinator.persona_value("persona")
        return float(persona.get(self._slider, 50))

    async def async_set_native_value(self, value: float) -> None:
        """Write the slider to the runtime (policy changes go through audit)."""
        await self.coordinator.async_set_persona(self._slider, int(value))
        self.async_write_ha_state()


class DailyBudgetNumber(PilotEntity, NumberEntity):
    """Hard daily LLM budget; the runtime stops the agent past this limit."""

    _attr_name = "Daily budget"
    _attr_native_min_value = 0
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "₽"

    def __init__(
        self,
        coordinator: PilotDataUpdateCoordinator,
        entry: PilotConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_daily_budget"

    @property
    def native_value(self) -> float:
        """Return the daily budget from runtime data or cache."""
        value = self.coordinator.persona_value("daily_budget")
        return float(value) if value is not None else 10.0

    async def async_set_native_value(self, value: float) -> None:
        """Write the budget to the runtime."""
        await self.coordinator.async_set_policy_value("daily_budget", value)
        self.async_write_ha_state()
