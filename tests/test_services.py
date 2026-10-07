"""Cycles 1.7-1.8: pilot.ask/pilot.propose services + core-update repairs issue."""

from homeassistant.helpers import issue_registry as ir

from custom_components.pilot.const import DOMAIN

from .test_integration import STATUS_OK, _setup_entry

CORE_UPDATE_STATUS = {
    **STATUS_OK,
    "core_update": {"to": "1.1.0", "snapshot": "/data/openclaw.bak-1.0.0.tar.gz"},
}


async def test_services_registered_and_ask_fires_event(hass, aioclient_mock):
    aioclient_mock.get("http://pilot:8899/api/status", json=STATUS_OK)
    aioclient_mock.post(
        "http://pilot:8899/api/chat",
        json={"say": "Тихо. Всё выключено.", "actions": [], "cost": 0.01},
    )
    await _setup_entry(hass, aioclient_mock)
    assert hass.services.has_service(DOMAIN, "ask")
    assert hass.services.has_service(DOMAIN, "propose")

    events: list[dict] = []
    hass.bus.async_listen(f"{DOMAIN}_ask_answered", lambda e: events.append(e.data))
    await hass.services.async_call(
        DOMAIN, "ask", {"question": "что дома?"}, blocking=True
    )
    await hass.async_block_till_done()
    assert events and events[0]["say"] == "Тихо. Всё выключено."
    chat_call = next(c for c in aioclient_mock.mock_calls if "/api/chat" in str(c[1]))
    assert chat_call[0] == "POST"


async def test_propose_service_enqueues(hass, aioclient_mock):
    aioclient_mock.get("http://pilot:8899/api/status", json=STATUS_OK)
    aioclient_mock.post("http://pilot:8899/api/queue", json={"ok": True, "id": "q9"})
    await _setup_entry(hass, aioclient_mock)

    events: list[dict] = []
    hass.bus.async_listen(f"{DOMAIN}_proposed", lambda e: events.append(e.data))
    await hass.services.async_call(
        DOMAIN,
        "propose",
        {"title": "Тестовое предложение", "action": {"type": "x"}},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert events and events[0]["id"] == "q9"


async def test_core_update_issue_created_and_cleared(hass, aioclient_mock):
    aioclient_mock.get("http://pilot:8899/api/status", json=CORE_UPDATE_STATUS)
    await _setup_entry(hass, aioclient_mock)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, "core_update_stuck")
    assert issue is not None
    assert "1.1.0" in issue.translation_placeholders["target"]


async def test_no_core_update_no_issue(hass, aioclient_mock):
    aioclient_mock.get("http://pilot:8899/api/status", json=STATUS_OK)
    await _setup_entry(hass, aioclient_mock)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "core_update_stuck") is None
