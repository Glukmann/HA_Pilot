"""Test the Pilot runtime API client."""

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.pilot.api import PilotApiClient
from custom_components.pilot.const import CannotConnect, PilotApiError

STATUS_PAYLOAD = {
    "status": "ok",
    "vitrine_age_s": 12,
    "cost_today": 0.38,
    "queue_size": 2,
    "runtime_version": "0.1.0",
}


async def _make_client(hass, aioclient_mock, status=200):
    client = PilotApiClient("pilot", 8899, "tok", async_get_clientsession(hass))
    aioclient_mock.get("http://pilot:8899/api/validate", status=status, json={})
    return client


async def test_async_validate_ok(hass, aioclient_mock):
    client = await _make_client(hass, aioclient_mock)
    await client.async_validate()


async def test_async_validate_401_raises_cannot_connect(hass, aioclient_mock):
    client = await _make_client(hass, aioclient_mock, status=401)
    with pytest.raises(CannotConnect):
        await client.async_validate()


async def test_async_validate_network_error_raises_cannot_connect(hass, aioclient_mock):
    client = PilotApiClient("pilot", 8899, "tok", async_get_clientsession(hass))
    aioclient_mock.get("http://pilot:8899/api/validate", exc=ClientError("net down"))
    with pytest.raises(CannotConnect):
        await client.async_validate()


async def test_async_validate_http_500_raises_api_error(hass, aioclient_mock):
    client = await _make_client(hass, aioclient_mock, status=500)
    with pytest.raises(PilotApiError):
        await client.async_validate()


async def test_async_get_status_parses_snapshot(hass, aioclient_mock):
    client = PilotApiClient("pilot", 8899, "tok", async_get_clientsession(hass))
    aioclient_mock.get("http://pilot:8899/api/status", status=200, json=STATUS_PAYLOAD)
    data = await client.async_get_status()
    assert data["status"] == "ok"
    assert data["queue_size"] == 2
    assert data["runtime_version"] == "0.1.0"
