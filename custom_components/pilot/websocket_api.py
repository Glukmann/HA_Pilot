"""WebSocket API for the Pilot panel (admin only)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.core import HomeAssistant, callback
import voluptuous as vol

from .const import DOMAIN
from .coordinator import PilotConfigEntry


def _entry(hass: HomeAssistant) -> PilotConfigEntry:
    """Return the single Pilot config entry."""
    entries: list[PilotConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    return entries[0]


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register Pilot websocket commands."""
    async_register_command(hass, ws_get_status)
    async_register_command(hass, ws_get_queue)
    async_register_command(hass, ws_confirm)
    async_register_command(hass, ws_get_vitrine)


@require_admin
@websocket_command({vol.Required("type"): "pilot/status"})
@async_response
async def ws_get_status(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the runtime status snapshot for the panel."""
    entry = _entry(hass)
    coordinator = entry.runtime_data
    connection.send_result(
        msg["id"],
        {
            "status": coordinator.data.get("status"),
            "vitrine_age_s": coordinator.data.get("vitrine_age_s"),
            "cost_today": coordinator.data.get("cost_today"),
            "queue_size": coordinator.data.get("queue_size"),
            "runtime_version": coordinator.data.get("runtime_version"),
            "last_update_success": coordinator.last_update_success,
        },
    )


@require_admin
@websocket_command({vol.Required("type"): "pilot/queue"})
@async_response
async def ws_get_queue(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the confirmation queue."""
    entry = _entry(hass)
    try:
        items = await entry.runtime_data.api.async_get_queue()
    except Exception:
        items = []
    connection.send_result(msg["id"], {"items": items})


@require_admin
@websocket_command(
    {
        vol.Required("type"): "pilot/confirm",
        vol.Required("item_id"): str,
        vol.Required("decision"): vol.Any("yes", "no"),
    }
)
@async_response
async def ws_confirm(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Approve or reject a confirmation queue item."""
    entry = _entry(hass)
    await entry.runtime_data.api.async_confirm(msg["item_id"], msg["decision"])
    await entry.runtime_data.async_request_refresh()
    connection.send_result(msg["id"], {"ok": True})


@require_admin
@websocket_command({vol.Required("type"): "pilot/vitrine"})
@async_response
async def ws_get_vitrine(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the home data vitrine for the panel table."""
    entry = _entry(hass)
    try:
        vitrine = await entry.runtime_data.api.async_get_vitrine()
    except Exception:
        vitrine = {"fresh": False, "lines": [], "error": "unreachable"}
    connection.send_result(msg["id"], vitrine)
