"""Diagnostics support for Pilot."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_RUNTIME_TOKEN
from .coordinator import PilotDataUpdateCoordinator

TO_REDACT = {CONF_RUNTIME_TOKEN, "token", "access_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with tokens and credentials redacted."""
    coordinator = cast("PilotDataUpdateCoordinator", entry.runtime_data)
    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "data": async_redact_data(
                dict(coordinator.data) if coordinator.data else {}, TO_REDACT
            ),
        },
    }
