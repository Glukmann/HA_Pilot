"""Test the vitrine push from the integration into the runtime."""

import asyncio
import threading

from .test_entities import FULL_STATUS, _post_mocks
from .test_integration import _mock_api, _setup_entry


async def test_state_change_from_foreign_thread_still_pushes(hass, aioclient_mock):
    """Bus listeners may fire from a non-loop thread; scheduling must survive it.

    A misbehaving integration can call async_set from the wrong thread — the
    listener must hop onto the HA loop instead of crashing loop.create_task
    (RuntimeError: Non-thread-safe operation).
    """
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})

    thread = threading.Thread(
        target=hass.states.async_set,
        args=("light.office", "on", {"friendly_name": "Office"}),
    )
    thread.start()
    thread.join()
    await asyncio.sleep(2.5)  # debounce window
    await hass.async_block_till_done()

    pushes = [c for c in aioclient_mock.mock_calls if "vitrine/update" in str(c[1])]
    assert len(pushes) == 1
    assert pushes[0][2]["states"]["light.office"]["state"] == "on"


async def test_pushed_states_include_area_name(hass, aioclient_mock):
    """Entities assigned to a HA area are pushed with their room name."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})

    living = ar.async_get(hass).async_create("Living room")
    entry = er.async_get(hass).async_get_or_create(
        "light", "test", "office", suggested_object_id="office"
    )
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=living.id)

    hass.states.async_set("light.office", "on", {"friendly_name": "Office"})
    await asyncio.sleep(2.5)  # debounce window
    await hass.async_block_till_done()

    pushes = [c for c in aioclient_mock.mock_calls if "vitrine/update" in str(c[1])]
    assert len(pushes) == 1
    assert pushes[0][2]["states"]["light.office"]["area"] == "Living room"


async def test_area_falls_back_to_device_area(hass, aioclient_mock):
    """An entity without its own area inherits the area of its device."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    _mock_api(aioclient_mock, payload=FULL_STATUS)
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})

    kitchen = ar.async_get(hass).async_create("Kitchen")
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    ce = MockConfigEntry(domain="test", data={})
    ce.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=ce.entry_id, connections={("mac", "aa:bb:cc:dd:ee:ff")}
    )
    dev_reg.async_update_device(device.id, area_id=kitchen.id)
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        "test",
        "temp",
        suggested_object_id="temp",
        device_id=device.id,
    )
    assert entry.area_id is None

    hass.states.async_set("sensor.temp", "22.4", {})
    await asyncio.sleep(2.5)
    await hass.async_block_till_done()

    pushes = [c for c in aioclient_mock.mock_calls if "vitrine/update" in str(c[1])]
    assert len(pushes) == 1
    assert pushes[0][2]["states"]["sensor.temp"]["area"] == "Kitchen"


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


async def test_initial_snapshot_seeds_vitrine(hass, aioclient_mock):
    """Pre-existing states are pushed via the initial snapshot.

    The push model only captures deltas — without the seed a fresh install's
    vitrine starts nearly empty (prod regression after a clean reinstall:
    the vitrine shrank from the full home to a few dozen changed entities).
    """
    _mock_api(aioclient_mock, payload=FULL_STATUS)
    hass.states.async_set("light.office", "on", {"friendly_name": "Office"})
    hass.states.async_set("sensor.temp", "22.4", {})
    await _setup_entry(hass, aioclient_mock)
    _post_mocks(aioclient_mock)
    aioclient_mock.post("http://pilot:8899/api/vitrine/update", status=200, json={})
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
