"""Test the Pilot coordinator, sensor, repairs and diagnostics."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.pilot.const import DOMAIN

STATUS_OK = {
    "status": "ok",
    "vitrine_age_s": 12,
    "cost_today": 0.38,
    "queue_size": 2,
    "runtime_version": "0.1.0",
}

ENTRY_DATA = {
    "runtime_host": "pilot",
    "runtime_port": 8899,
    "runtime_token": "eyJhbGciOiJIUzI1NiJ9.secret-token-part",
}


def _mock_api(aioclient_mock: AiohttpClientMocker, status=200, payload=None):
    aioclient_mock.get(
        "http://pilot:8899/api/status",
        status=status,
        json=payload if payload is not None else STATUS_OK,
    )


async def _setup_entry(hass, aioclient_mock, **kwargs):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, **kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_coordinator_update_success(hass, aioclient_mock):
    """Test the coordinator fetches the status snapshot on setup."""
    _mock_api(aioclient_mock)
    entry = await _setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.data["status"] == "ok"
    assert coordinator.update_interval.total_seconds() == 30


async def test_coordinator_failure_marks_unavailable(hass, aioclient_mock):
    """Test unreachable runtime: entry loads, sensor goes unavailable."""
    _mock_api(aioclient_mock, status=500)
    entry = await _setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.last_update_success is False

    state = hass.states.get("sensor.pilot_status")
    assert state is not None
    assert state.state == "unavailable"


async def test_sensor_reports_status_and_attributes(hass, aioclient_mock):
    """Test sensor.pilot_status value and attributes from the snapshot."""
    _mock_api(aioclient_mock)
    await _setup_entry(hass, aioclient_mock)

    state = hass.states.get("sensor.pilot_status")
    assert state.state == "ok"
    assert state.attributes["vitrine_age_s"] == 12
    assert state.attributes["queue_size"] == 2
    assert state.attributes["runtime_version"] == "0.1.0"

    device = hass.states.get("sensor.pilot_status")
    assert device.attributes.get("friendly_name") is not None


async def test_repairs_created_when_down_and_removed_when_back(hass, aioclient_mock):
    """Test the runtime_unreachable repair appears and clears."""
    _mock_api(aioclient_mock, status=500)
    entry = await _setup_entry(hass, aioclient_mock)
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, "runtime_unreachable") is not None

    aioclient_mock.clear_requests()
    _mock_api(aioclient_mock)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, "runtime_unreachable") is None


async def test_diagnostics_redacts_token(hass, aioclient_mock):
    """Test diagnostics output never contains the configured token."""
    from custom_components.pilot.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    _mock_api(aioclient_mock)
    entry = await _setup_entry(hass, aioclient_mock)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    import json

    serialized = json.dumps(diag)
    assert "secret-token-part" not in serialized
    assert "eyJhbGciOiJIUzI1NiJ9" not in serialized


async def test_system_health_reports_runtime(hass, aioclient_mock):
    """Test system health info includes runtime status and version."""
    from custom_components.pilot.system_health import system_health_info

    _mock_api(aioclient_mock)
    aioclient_mock.get("http://pilot:8899/api/validate", status=200, json={})
    await _setup_entry(hass, aioclient_mock)
    info = await system_health_info(hass)
    assert info["runtime_status"] == "ok"
    assert info["runtime_version"] == "0.1.0"
    assert info["can_reach_runtime"] == "true"
