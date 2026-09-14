"""System health for Pilot."""

from __future__ import annotations

from homeassistant.components.system_health import SystemHealthRegistration
from homeassistant.core import HomeAssistant, callback

from .const import CannotConnect
from .coordinator import PilotConfigEntry


@callback
def async_register(hass: HomeAssistant, register: SystemHealthRegistration) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, str]:
    """Return info for the system health panel."""
    entries: list[PilotConfigEntry] = hass.config_entries.async_entries("pilot")
    if not entries:
        return {"can_reach_runtime": "no entry"}
    coordinator = entries[0].runtime_data
    info: dict[str, str] = {
        "runtime_status": str(coordinator.data.get("status", "unknown")),
        "runtime_version": str(coordinator.data.get("runtime_version", "unknown")),
        "can_reach_runtime": str(coordinator.last_update_success).lower(),
    }
    try:
        await coordinator.api.async_validate()
    except CannotConnect:
        info["can_reach_runtime"] = "false"
    return info
