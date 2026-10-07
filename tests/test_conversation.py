"""Tests for the Pilot conversation agent (Assist chat remote control)."""

from homeassistant.components import conversation
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.pilot.const import DOMAIN

from .test_integration import ENTRY_DATA, STATUS_OK


def _mock_api(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get("http://pilot:8899/api/status", json=STATUS_OK)


def _chat_payload(say: str, actions: list) -> dict:
    return {"say": say, "actions": actions, "cost": 0.01}


async def _setup_agent(hass, aioclient_mock: AiohttpClientMocker):
    """Set up the config entry and return the conversation entity."""
    from homeassistant.setup import async_setup_component

    # The conversation default agent needs homeassistant.exposed_entities,
    # created by the homeassistant component (not set up in this harness).
    await async_setup_component(hass, "homeassistant", {})
    _mock_api(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "conversation", DOMAIN, f"{entry.entry_id}_conversation"
    )
    agent = hass.data[conversation.DOMAIN].get_entity(entity_id)
    return entry, agent


def _input(text: str) -> conversation.ConversationInput:
    return conversation.ConversationInput(
        text=text,
        context=Context(),
        conversation_id="test-conv",
        device_id=None,
        satellite_id=None,
        language="ru",
        agent_id="pilot",
        extra_system_prompt=None,
    )


async def test_chat_agent_registered(hass, aioclient_mock):
    _entry, agent = await _setup_agent(hass, aioclient_mock)
    assert agent.supported_features & conversation.ConversationEntityFeature.CONTROL


async def test_direct_action_executed_in_ha(hass, aioclient_mock):
    """A whitelisted action runs through HA services; the rest is enqueued."""
    calls: list[tuple[str, str, dict]] = []
    hass.services.async_register(
        "pilot_test",
        "poke",
        lambda call: calls.append(("pilot_test", "poke", dict(call.data))),
    )
    _mock_api(aioclient_mock)
    aioclient_mock.post(
        "http://pilot:8899/api/chat",
        json=_chat_payload(
            "Готово.",
            [
                {
                    "domain": "pilot_test",
                    "service": "poke",
                    "entity_id": "pilot_test.demo",
                    "data": {"brightness": 3},
                    "mode": "direct",
                },
                {
                    "domain": "switch",
                    "service": "turn_on",
                    "entity_id": "switch.pump",
                    "data": {},
                    "mode": "queue",
                },
            ],
        ),
    )
    aioclient_mock.post("http://pilot:8899/api/queue", json={"ok": True, "id": "q1"})
    _entry, agent = await _setup_agent(hass, aioclient_mock)

    result = await agent.async_process(_input("выключи свет в холле"))
    speech = result.response.speech["plain"]["speech"]
    assert "Готово." in speech
    assert "Выполнено: pilot_test.demo" in speech  # deterministic accounting
    assert "мастерской" in speech  # queued action disclosed

    assert calls == [
        ("pilot_test", "poke", {"brightness": 3, "entity_id": "pilot_test.demo"})
    ]
    queue_call = aioclient_mock.mock_calls[-1]
    assert queue_call[0] == "POST"
    assert "/api/queue" in str(queue_call[1])


async def test_question_without_actions(hass, aioclient_mock):
    _mock_api(aioclient_mock)
    aioclient_mock.post(
        "http://pilot:8899/api/chat",
        json=_chat_payload("В гостиной 22.3°C, окна закрыты.", []),
    )
    _entry, agent = await _setup_agent(hass, aioclient_mock)

    result = await agent.async_process(_input("что в гостиной?"))
    assert (
        result.response.speech["plain"]["speech"] == "В гостиной 22.3°C, окна закрыты."
    )
    # No actions -> no queue POST.
    assert all("/api/queue" not in str(call) for call in aioclient_mock.mock_calls)


async def test_refused_action_noted(hass, aioclient_mock):
    _mock_api(aioclient_mock)
    aioclient_mock.post(
        "http://pilot:8899/api/chat",
        json=_chat_payload(
            "Не могу.",
            [
                {
                    "domain": "camera",
                    "service": "turn_on",
                    "entity_id": "camera.porch",
                    "data": {},
                    "mode": "refuse",
                }
            ],
        ),
    )
    _entry, agent = await _setup_agent(hass, aioclient_mock)

    result = await agent.async_process(_input("включи камеру"))
    speech = result.response.speech["plain"]["speech"]
    assert "Камеры" in speech or "камеры" in speech


async def test_runtime_down_answers_politely(hass, aioclient_mock):
    """Soft degradation: the chat answers instead of erroring out."""
    from homeassistant.setup import async_setup_component

    await async_setup_component(hass, "homeassistant", {})
    aioclient_mock.get("http://pilot:8899/api/status", status=500)
    aioclient_mock.post("http://pilot:8899/api/chat", status=500)
    _entry, agent = await _setup_agent(hass, aioclient_mock)

    result = await agent.async_process(_input("привет"))
    assert "недоступен" in result.response.speech["plain"]["speech"]


async def test_failed_service_call_disclosed(hass, aioclient_mock):
    """An unknown service does not crash the turn; it is disclosed."""
    _mock_api(aioclient_mock)
    aioclient_mock.post(
        "http://pilot:8899/api/chat",
        json=_chat_payload(
            "Пытаюсь.",
            [
                {
                    "domain": "no_such_domain",
                    "service": "poke",
                    "entity_id": "no_such_domain.x",
                    "data": {},
                    "mode": "direct",
                }
            ],
        ),
    )
    _entry, agent = await _setup_agent(hass, aioclient_mock)

    result = await agent.async_process(_input("сделай что-нибудь"))
    speech = result.response.speech["plain"]["speech"]
    assert "не удалась" in speech
