"""Test the vitrine push from the integration into the runtime."""

import asyncio

from .test_entities import FULL_STATUS, _post_mocks
from .test_integration import _mock_api, _setup_entry


async def test_state_changes_push_to_runtime(hass, aioclient_mock):
    """Test entity changes are debounced and POSTed to /api/vitrine/update."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})

    hass.states.async_set("light.office", "on", {"friendly_name": "Office"})
    hass.states.async_set("sensor.temp", "22.4", {})
    await asyncio.sleep(2.5)  # debounce window
    await hass.async_block_till_done()

    pushes = [c for c in aioclient_mock.mock_calls if "vitrine/update" in str(c[1])]
    assert len(pushes) == 1
    states = pushes[0][2]["states"]
    assert states["light.office"]["state"] == "on"
    assert states["sensor.temp"]["state"] == "22.4"


async def test_ignored_domains_not_pushed(hass, aioclient_mock):
    """Test automation/script/zone changes are not forwarded."""
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})
    await asyncio.sleep(2.5)  # let setup-time entity changes flush out
    aioclient_mock.clear_requests()
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})

    hass.states.async_set("automation.night", "on", {})
    await asyncio.sleep(2.5)
    await hass.async_block_till_done()

    pushes = [c for c in aioclient_mock.mock_calls if "vitrine/update" in str(c[1])]
    assert pushes == []
