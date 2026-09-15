"""Tests for Phase 2 device stack entities."""

from custom_components.pilot.const import DOMAIN

from .test_integration import _mock_api, _setup_entry

FULL_STATUS = {
    "status": "ok",
    "vitrine_age_s": 12,
    "cost_today": 1.25,
    "queue_size": 2,
    "awaiting_confirmation": True,
    "runtime_version": "0.2.0",
    "persona": {
        "butler_observer": 70,
        "politeness": 40,
        "verbosity": 30,
        "conservative": 60,
    },
    "daily_budget": 10.0,
    "persona_preset": "butler",
    "mode": "normal",
    "current_focus": "Night energy savings",
}


async def _setup_full(hass, aioclient_mock):
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    return await _setup_entry(hass, aioclient_mock)


def _post_mocks(aioclient_mock):
    """Register POST endpoints used by write-through entities."""
    aioclient_mock.post("http://pilot:8899/api/persona", status=200, json={})
    aioclient_mock.post("http://pilot:8899/api/budget", status=200, json={})
    aioclient_mock.post("http://pilot:8899/api/preset", status=200, json={})
    aioclient_mock.post("http://pilot:8899/api/mode", status=200, json={})
    aioclient_mock.post("http://pilot:8899/api/focus", status=200, json={})
    aioclient_mock.post("http://pilot:8899/api/reset", status=200, json={})


async def test_sensors_report_values(hass, aioclient_mock):
    """Test cost/suggestions sensors from the full snapshot."""
    await _setup_full(hass, aioclient_mock)
    assert hass.states.get("sensor.pilot_eyes_cost_today").state == "1.25"
    assert hass.states.get("sensor.pilot_eyes_pending_suggestions").state == "2"


async def test_binary_sensors_report_values(hass, aioclient_mock):
    """Test freshness and confirmation binary sensors."""
    await _setup_full(hass, aioclient_mock)
    assert hass.states.get("binary_sensor.pilot_eyes_data_fresh").state == "on"
    assert (
        hass.states.get("binary_sensor.pilot_eyes_awaiting_confirmation").state == "on"
    )


async def test_data_fresh_off_when_stale(hass, aioclient_mock):
    """Test stale vitrine marks data_fresh off."""
    stale = {**FULL_STATUS, "vitrine_age_s": 300}
    _mock_api(aioclient_mock, payload=stale)
    await _setup_entry(hass, aioclient_mock)
    assert hass.states.get("binary_sensor.pilot_eyes_data_fresh").state == "off"


async def test_persona_sliders_from_snapshot_and_write(hass, aioclient_mock):
    """Test slider values and write-through to the runtime."""
    entry = await _setup_full(hass, aioclient_mock)
    assert hass.states.get("number.pilot_eyes_persona_butler_observer").state == "70.0"

    _post_mocks(aioclient_mock)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.pilot_eyes_persona_butler_observer", "value": 20},
        blocking=True,
    )
    assert entry.runtime_data.persona_cache["persona"]["butler_observer"] == 20
    _method, url, body, _headers = aioclient_mock.mock_calls[-1]
    assert "/api/persona" in str(url)
    assert body == {"slider": "butler_observer", "value": 20}


async def test_daily_budget_write(hass, aioclient_mock):
    """Test budget number writes to /api/budget."""
    entry = await _setup_full(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.pilot_eyes_daily_budget", "value": 25},
        blocking=True,
    )
    assert entry.runtime_data.persona_cache["daily_budget"] == 25.0
    assert "/api/budget" in str(aioclient_mock.mock_calls[-1][1])
    assert aioclient_mock.mock_calls[-1][2] == {"value": 25.0}


async def test_selects_report_and_write(hass, aioclient_mock):
    """Test preset/mode selects from snapshot and write-through."""
    entry = await _setup_full(hass, aioclient_mock)
    assert hass.states.get("select.pilot_eyes_persona_preset").state == "butler"
    assert hass.states.get("select.pilot_eyes_home_mode").state == "normal"

    _post_mocks(aioclient_mock)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.pilot_eyes_home_mode", "option": "vacation"},
        blocking=True,
    )
    assert entry.runtime_data.persona_cache["mode"] == "vacation"
    assert "/api/mode" in str(aioclient_mock.mock_calls[-1][1])
    assert aioclient_mock.mock_calls[-1][2] == {"mode": "vacation"}


async def test_reset_buttons_call_runtime(hass, aioclient_mock):
    """Test buttons call /api/reset with the right target."""
    await _setup_full(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.pilot_eyes_reset_learning"},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"target": "learning"}

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.pilot_eyes_reset_all"},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"target": "all"}


async def test_current_focus_text_write(hass, aioclient_mock):
    """Test text entity writes focus to the runtime."""
    entry = await _setup_full(hass, aioclient_mock)
    assert hass.states.get("text.pilot_eyes_current_focus").state == (
        "Night energy savings"
    )
    _post_mocks(aioclient_mock)
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.pilot_eyes_current_focus", "value": "Morning comfort"},
        blocking=True,
    )
    assert entry.runtime_data.persona_cache["current_focus"] == "Morning comfort"
    assert "/api/focus" in str(aioclient_mock.mock_calls[-1][1])


async def test_all_entities_on_one_device(hass, aioclient_mock):
    """Test every entity belongs to the single 'Pilot Eyes' device."""
    await _setup_full(hass, aioclient_mock)
    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    devices = {
        entry.device_id
        for entry in entity_registry.entities.values()
        if entry.platform == DOMAIN
    }
    assert len(devices) == 1
