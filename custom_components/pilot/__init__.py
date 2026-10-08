"""The Pilot integration — proactive AI agent for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import PilotConfigEntry, PilotDataUpdateCoordinator
from .notify import async_setup_notify
from .repairs import async_sync_core_update_issue, async_sync_repairs_issue
from .vitrine_push import async_setup_vitrine_push
from .websocket_api import async_register_commands

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.TEXT,
    Platform.CONVERSATION,
]

ATTR_QUESTION = "question"
ATTR_TITLE = "title"
ATTR_ACTION = "action"
ATTR_SUMMARY = "summary"


def _core_update_from(
    coordinator: PilotDataUpdateCoordinator,
) -> dict[str, object] | None:
    data = coordinator.data or {}
    core_update = data.get("core_update")
    return core_update if isinstance(core_update, dict) else None


async def _async_setup_services(hass: HomeAssistant, entry: PilotConfigEntry) -> None:
    """Register pilot.ask / pilot.propose for HA automations (cycle 1.7)."""

    async def handle_ask(call: ServiceCall) -> None:
        coordinator = entry.runtime_data
        result = await coordinator.api.async_ask(str(call.data[ATTR_QUESTION]))
        hass.bus.async_fire(
            f"{DOMAIN}_ask_answered",
            {
                "question": str(call.data[ATTR_QUESTION]),
                "say": result.get("say", ""),
                "error": result.get("error", ""),
            },
        )

    async def handle_propose(call: ServiceCall) -> None:
        coordinator = entry.runtime_data
        item_id = await coordinator.api.async_enqueue(
            title=str(call.data[ATTR_TITLE]),
            action=dict(call.data[ATTR_ACTION]),
            summary=str(call.data.get(ATTR_SUMMARY) or ""),
        )
        hass.bus.async_fire(f"{DOMAIN}_proposed", {"id": item_id})

    hass.services.async_register(
        DOMAIN,
        "ask",
        handle_ask,
        schema=vol.Schema({vol.Required(ATTR_QUESTION): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        "propose",
        handle_propose,
        schema=vol.Schema(
            {
                vol.Required(ATTR_TITLE): cv.string,
                vol.Required(ATTR_ACTION): dict,
                vol.Optional(ATTR_SUMMARY): cv.string,
            }
        ),
    )


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
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: async_sync_core_update_issue(
                hass, entry.entry_id, _core_update_from(coordinator)
            )
        )
    )
    async_sync_repairs_issue(hass, entry.entry_id, not coordinator.last_update_success)
    async_sync_core_update_issue(hass, entry.entry_id, _core_update_from(coordinator))

    # The single user-facing UI is the add-on's "Пилот" sidebar panel (the
    # workshop SPA behind ingress); this integration exposes entities,
    # services and the pilot/* websocket API only — no own panel.
    async_register_commands(hass)
    await async_setup_notify(hass, entry)
    await async_setup_vitrine_push(hass, entry)
    await _async_setup_services(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PilotConfigEntry) -> bool:
    """Unload a config entry."""
    async_sync_repairs_issue(hass, entry.entry_id, False)
    async_sync_core_update_issue(hass, entry.entry_id, None)
    hass.services.async_remove(DOMAIN, "ask")
    hass.services.async_remove(DOMAIN, "propose")
    return bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))
