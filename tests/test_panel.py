"""Tests for the Pilot panel websocket API and actionable notifications."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import custom_components.pilot

from .test_entities import FULL_STATUS, _post_mocks
from .test_integration import _mock_api, _setup_entry

QUEUE = [
    {"id": "q1", "title": "Raise bedroom setpoint to 22°C?", "summary": "night drift"},
    {"id": "q2", "title": "Suggest movie automation?", "summary": "3rd evening"},
]


async def test_panel_registered_as_custom_component(hass, aioclient_mock):
    """Regression: the frontend router only knows how to load component_name="custom".

    Any other value makes partial-panel-resolver create an undefined
    ha-panel-<name> element — the sidebar entry exists but the page renders
    as a black screen.
    """
    _mock_api(aioclient_mock)
    with patch("custom_components.pilot.async_register_built_in_panel") as register:
        await _setup_entry(hass, aioclient_mock)
    register.assert_called_once()
    assert register.call_args.kwargs["component_name"] == "custom"
    assert register.call_args.kwargs["frontend_url_path"] == "pilot"
    panel_config = register.call_args.kwargs["config"]["_panel_custom"]
    assert panel_config["name"] == "pilot-panel"
    assert panel_config["js_url"].startswith("/pilot_static/panel.js?v=")
    expected = hashlib.sha256(
        (
            Path(custom_components.pilot.__file__).parent / "frontend" / "panel.js"
        ).read_bytes()
    ).hexdigest()[:8]
    assert panel_config["js_url"].endswith(f"v={expected}")


async def test_ws_status(hass, aioclient_mock, hass_ws_client):
    """Test pilot/status returns the snapshot."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "pilot/status"})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["status"] == "ok"
    assert msg["result"]["queue_size"] == 2


async def test_ws_queue_and_confirm(hass, aioclient_mock, hass_ws_client):
    """Test pilot/queue returns items and pilot/confirm posts the decision."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    entry = await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.get("http://pilot:8899/api/queue", status=200, json={"items": QUEUE})
    aioclient_mock.post(
        "http://pilot:8899/api/queue/confirm", status=200, json={"ok": True}
    )

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "pilot/queue"})
    msg = await client.receive_json()
    assert msg["success"]
    assert [i["id"] for i in msg["result"]["items"]] == ["q1", "q2"]

    await client.send_json(
        {"id": 2, "type": "pilot/confirm", "item_id": "q1", "decision": "yes"}
    )
    msg = await client.receive_json()
    assert msg["success"]
    confirm_calls = [
        c for c in aioclient_mock.mock_calls if "queue/confirm" in str(c[1])
    ]
    assert len(confirm_calls) == 1
    assert confirm_calls[0][2] == {"id": "q1", "decision": "yes"}
    assert entry.runtime_data.last_update_success


async def test_ws_vitrine(hass, aioclient_mock, hass_ws_client):
    """Test pilot/vitrine returns lines and freshness."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.get(
        "http://pilot:8899/api/vitrine",
        status=200,
        json={"fresh": True, "lines": ["light.office: ON", "climate: 22°C"]},
    )
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "pilot/vitrine"})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["fresh"] is True
    assert len(msg["result"]["lines"]) == 2


async def test_notify_sent_when_queue_appears(hass, aioclient_mock):
    """Test an actionable notification fires when awaiting_confirmation turns on."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    entry = await _setup_entry(hass, aioclient_mock)

    notifications: list[dict] = []

    async def _mock_notify(service_call):
        notifications.append(service_call.data)

    hass.services.async_register("notify", "notify", _mock_notify)

    # Simulate queue draining then refilling via two refreshes.
    empty = {**FULL_STATUS, "queue_size": 0, "awaiting_confirmation": False}
    aioclient_mock.clear_requests()
    _mock_api(aioclient_mock, payload=empty)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    refilled = {**FULL_STATUS, "queue_size": 3, "awaiting_confirmation": True}
    aioclient_mock.clear_requests()
    _mock_api(aioclient_mock, payload=refilled)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(notifications) == 1
    assert notifications[0]["title"] == "Pilot awaits confirmation"
    actions = [a["action"] for a in notifications[0]["data"]["actions"]]
    assert actions == ["pilot_confirm_yes", "pilot_confirm_no"]


async def test_mobile_action_confirms_oldest(hass, aioclient_mock):
    """Test a mobile_app action event confirms the oldest queue item."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.get("http://pilot:8899/api/queue", status=200, json={"items": QUEUE})
    aioclient_mock.post(
        "http://pilot:8899/api/queue/confirm", status=200, json={"ok": True}
    )

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "pilot_confirm_yes", "sourceDeviceID": "phone"},
    )
    await hass.async_block_till_done()

    confirm_calls = [
        c for c in aioclient_mock.mock_calls if "queue/confirm" in str(c[1])
    ]
    assert len(confirm_calls) == 1
    assert confirm_calls[0][2] == {"id": "q1", "decision": "yes"}


async def test_reload_does_not_fail_on_panel_reregistration(hass, aioclient_mock):
    """Regression: re-setup after unload used to die with 'Overwriting panel'.

    HA retries failed setups and reloads the entry on options changes, and
    unload does not unregister the panel — so the second setup raised
    ValueError and wedged the entry in SETUP_ERROR until an HA restart.
    Downstream effect: no vitrine pushes, devices disappear from the panel.
    """
    from homeassistant.config_entries import ConfigEntryState

    _mock_api(aioclient_mock)
    entry = await _setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
