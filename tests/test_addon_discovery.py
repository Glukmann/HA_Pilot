"""Test publishing the add-on to HA via Supervisor discovery."""

import aiohttp
from pilot_addon.discovery import publish_discovery


async def test_publish_sends_discovery_to_supervisor(hass, aioclient_mock, monkeypatch):
    """With a supervisor token the service is announced with host and port."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    aioclient_mock.post("http://supervisor/discovery", status=200, json={"uuid": "u1"})

    await publish_discovery(async_get_clientsession(hass), port=8899)

    assert len(aioclient_mock.mock_calls) == 1
    _method, url, payload, headers = aioclient_mock.mock_calls[0]
    assert str(url) == "http://supervisor/discovery"
    assert payload["service"] == "pilot"
    assert payload["config"]["host"]
    assert payload["config"]["port"] == 8899
    assert headers["Authorization"] == "Bearer supervisor-secret"


async def test_publish_skipped_without_supervisor_token(
    hass, aioclient_mock, monkeypatch
):
    """Standalone instances (no token) stay silent — no supervisor exists."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    await publish_discovery(async_get_clientsession(hass), port=8899)

    assert aioclient_mock.mock_calls == []


async def test_publish_survives_supervisor_error(hass, aioclient_mock, monkeypatch):
    """A failing supervisor must never crash the add-on startup."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    aioclient_mock.post(
        "http://supervisor/discovery", status=403, json={"error": "nope"}
    )

    await publish_discovery(async_get_clientsession(hass), port=8899)  # must not raise

    assert len(aioclient_mock.mock_calls) == 1  # the attempt was really made


class _RefusingSession:
    """Session whose POST raises a connection error on enter."""

    def post(self, *_args, **_kwargs):
        class _Ctx:
            async def __aenter__(self):
                raise aiohttp.ClientConnectionError("connection refused")

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


async def test_publish_survives_unreachable_supervisor(monkeypatch):
    """Network-level failure is logged and swallowed."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")

    await publish_discovery(_RefusingSession(), port=8899)  # must not raise
