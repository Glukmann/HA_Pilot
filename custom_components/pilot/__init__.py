"""The Pilot integration — proactive AI agent for Home Assistant."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PilotConfigEntry, PilotDataUpdateCoordinator
from .notify import async_setup_notify
from .repairs import async_sync_repairs_issue
from .websocket_api import async_register_commands

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.TEXT,
]

FRONTEND_DIR = Path(__file__).parent / "frontend"
PANEL_URL_PATH = "pilot"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_MODULE_URL = f"{STATIC_URL}/panel.js"


async def _async_update_listener(hass: HomeAssistant, entry: PilotConfigEntry) -> None:
    """Reload on config entry updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: PilotConfigEntry) -> bool:
    """Set up Pilot from a config entry."""
    coordinator = PilotDataUpdateCoordinator(hass, entry)
    await coordinator.async_load_cache()
    # Soft degradation: never fail setup when the runtime is down — the home
    # keeps working as plain HA; only Pilot features pause (see repairs issue).
    await coordinator.async_refresh()
    entry.runtime_data = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: async_sync_repairs_issue(
                hass, entry.entry_id, not coordinator.last_update_success
            )
        )
    )
    async_sync_repairs_issue(hass, entry.entry_id, not coordinator.last_update_success)

    async_register_commands(hass)
    await async_register_panel(hass)
    await async_setup_notify(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the sidebar panel and its static frontend."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )
    async_register_built_in_panel(
        hass,
        component_name=PANEL_URL_PATH,
        sidebar_title="Pilot",
        sidebar_icon="mdi:robot-outline",
        frontend_url_path=PANEL_URL_PATH,
        require_admin=True,
        config={
            "_custom_panel": {
                "name": "pilot-panel",
                "js_url": PANEL_MODULE_URL,
                "embed_iframe": False,
                "trust_external": False,
            }
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: PilotConfigEntry) -> bool:
    """Unload a config entry."""
    async_sync_repairs_issue(hass, entry.entry_id, False)
    return bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))
