"""Actionable notifications for the Pilot confirmation queue.

Web notifications have no buttons (home-assistant/frontend#4411), so
confirmations on a phone go through mobile_app actionable notifications.
The panel remains the base channel.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback

from .coordinator import PilotConfigEntry

EVENT_MOBILE_ACTION = "mobile_app_notification_action"
ACTION_CONFIRM_YES = "pilot_confirm_yes"
ACTION_CONFIRM_NO = "pilot_confirm_no"


async def async_setup_notify(hass: HomeAssistant, entry: PilotConfigEntry) -> None:
    """Push a notification when the trust loop starts awaiting confirmation."""
    coordinator = entry.runtime_data
    state = {"notified": False}

    async def _maybe_notify() -> None:
        if not coordinator.last_update_success:
            return
        queue_size = int(coordinator.data.get("queue_size") or 0)
        awaiting = bool(coordinator.data.get("awaiting_confirmation")) or queue_size > 0
        if awaiting and not state["notified"]:
            state["notified"] = True
            await _async_send(hass, queue_size)
        elif not awaiting:
            state["notified"] = False

    @callback
    def _handle_mobile_action(event: Any) -> None:
        action = event.data.get("action")
        if action not in (ACTION_CONFIRM_YES, ACTION_CONFIRM_NO):
            return

        async def _confirm() -> None:
            decision = "yes" if action == ACTION_CONFIRM_YES else "no"
            try:
                queue = await coordinator.api.async_get_queue()
            except Exception:
                return
            if queue:
                await coordinator.api.async_confirm(str(queue[0]["id"]), decision)
                await coordinator.async_request_refresh()

        hass.async_create_task(_confirm())

    @callback
    def _on_coordinator_update() -> None:
        hass.async_create_task(_maybe_notify())

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_MOBILE_ACTION, _handle_mobile_action)
    )


async def _async_send(hass: HomeAssistant, queue_size: int) -> None:
    """Send one actionable notification through the default notify service."""
    if not hass.services.has_service("notify", "notify"):
        return
    await hass.services.async_call(
        "notify",
        "notify",
        {
            "title": "Pilot awaits confirmation",
            "message": f"{queue_size} suggestion(s) need your decision.",
            "data": {
                "actions": [
                    {"action": ACTION_CONFIRM_YES, "title": "Yes"},
                    {"action": ACTION_CONFIRM_NO, "title": "No"},
                ]
            },
        },
        blocking=False,
    )
