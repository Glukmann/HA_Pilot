"""End-to-end contract test: HA integration client <-> add-on HTTP API."""

from aiohttp import web
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state

from custom_components.pilot.api import PilotApiClient
from custom_components.pilot.const import CannotConnect


async def _start_addon(tmp_path, token=""):
    state = build_state(tmp_path)
    state.token = token
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return state, runner, port


async def test_contract_roundtrip(tmp_path, hass, socket_enabled):
    """Exercise the full /api contract through the integration's client."""
    state, runner, port = await _start_addon(tmp_path)
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    client = PilotApiClient("127.0.0.1", port, "", async_get_clientsession(hass))

    await client.async_validate()
    status = await client.async_get_status()
    assert status["status"] == "ok"
    assert status["persona"]["politeness"] == 50
    assert status["awaiting_confirmation"] is False

    await client.async_set_persona("verbosity", 80)
    assert state.persona["verbosity"] == 80
    await client.async_set_daily_budget(25.0)
    await client.async_set_persona_preset("observer")
    await client.async_set_mode("vacation")
    await client.async_set_current_focus("Night savings")
    assert state.daily_budget == 25.0
    assert state.persona_preset == "observer"
    assert state.mode == "vacation"
    assert state.current_focus == "Night savings"

    item_id = state.queue.propose("Test proposal?", {"type": "switch"})
    queue = await client.async_get_queue()
    assert [i["id"] for i in queue] == [item_id]
    await client.async_confirm(item_id, "yes")
    assert await client.async_get_queue() == []

    await client.async_push_vitrine(
        {
            "light.office": {
                "state": "on",
                "attrs": {"friendly_name": "Office light"},
            },
            "sensor.temp": {"state": "22.4", "attrs": {}},
        }
    )
    vitrine = await client.async_get_vitrine()
    assert vitrine["fresh"] is True  # push just arrived
    assert "No room:" in vitrine["lines"]
    assert "  Office light: on" in vitrine["lines"]
    assert "  sensor.temp: 22.4" in vitrine["lines"]

    await client.async_reset("learning")
    await runner.cleanup()


async def test_token_enforced(tmp_path, hass, socket_enabled):
    """Unauthorized requests to /api/validate are rejected."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    _state, runner, port = await _start_addon(tmp_path, token="secret")
    client = PilotApiClient("127.0.0.1", port, "wrong", async_get_clientsession(hass))
    try:
        await client.async_validate()
        raised = False
    except CannotConnect:
        raised = True
    assert raised
    await runner.cleanup()


def test_vitrine_lines_grouped_by_area(tmp_path):
    """Vitrine lines group entities under their room names; unassigned last."""
    state = build_state(tmp_path)
    state.vitrine.update(
        {
            "light.office": {
                "state": "on",
                "attrs": {"friendly_name": "Office"},
                "area": "Bedroom",
            },
            "sensor.temp": {
                "state": "22.4",
                "attrs": {"friendly_name": "Temp"},
                "area": "Bedroom",
            },
            "switch.misc": {"state": "off", "attrs": {"friendly_name": "Misc"}},
        }
    )
    lines = state.vitrine.lines
    assert lines[0] == "Bedroom:"
    assert lines[1] == "  Office: on"
    assert lines[2] == "  Temp: 22.4"
    assert lines[3] == "No room:"
    assert lines[4] == "  Misc: off"
