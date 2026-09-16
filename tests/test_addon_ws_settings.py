"""Tests for the WS setter commands (persona/budget/mode) and config/get."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json

import aiohttp
from aiohttp import WSMsgType, web
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state
from pilot_addon.state import PERSONA_PRESETS, RuntimeState
import pytest


@pytest.fixture
async def addon(tmp_path, socket_enabled) -> AsyncIterator[tuple[RuntimeState, int]]:
    """Run the add-on app on an ephemeral port (same pattern as test_addon_ws)."""
    state = build_state(tmp_path)
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield state, port
    await runner.cleanup()


async def _rpc(ws: aiohttp.ClientWebSocketResponse, cmd: str, payload: dict) -> dict:
    """Send a command and wait for its response frame."""
    await ws.send_str(json.dumps({"type": cmd, "payload": payload}))
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    assert msg.type == WSMsgType.TEXT, msg
    return json.loads(msg.data)


async def _next_event(ws: aiohttp.ClientWebSocketResponse) -> dict:
    """Wait for the next inbound frame (event or response)."""
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    assert msg.type == WSMsgType.TEXT, msg
    return json.loads(msg.data)


async def test_persona_set_ok_and_validation(addon) -> None:
    """persona/set applies a slider; bad slider/value answer an error frame."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "persona/set", {"slider": "politeness", "value": 80})
            assert reply == {"type": "persona/set", "payload": {"ok": True}}
            assert state.persona["politeness"] == 80
            # Success broadcasts a fresh status snapshot to this connection too.
            event = await _next_event(ws)
            assert event["type"] == "status"
            assert event["payload"]["persona"]["politeness"] == 80

            # String numbers are accepted (same coercion as the REST API).
            reply = await _rpc(
                ws, "persona/set", {"slider": "verbosity", "value": "65"}
            )
            assert reply["payload"] == {"ok": True}
            assert state.persona["verbosity"] == 65
            assert (await _next_event(ws))["type"] == "status"

            reply = await _rpc(ws, "persona/set", {"slider": "nope", "value": 50})
            assert reply["type"] == "error"
            assert "unknown slider" in reply["payload"]["message"]

            reply = await _rpc(
                ws, "persona/set", {"slider": "politeness", "value": "abc"}
            )
            assert reply["type"] == "error"

            reply = await _rpc(ws, "persona/set", {"slider": "politeness"})
            assert reply["type"] == "error"
            assert "missing field" in reply["payload"]["message"]

            # Connection survived the validation errors.
            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_preset_apply_ok_and_validation(addon) -> None:
    """preset/apply applies a named preset; unknown preset is an error."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "preset/apply", {"preset": "economy"})
            assert reply == {"type": "preset/apply", "payload": {"ok": True}}
            assert state.persona == PERSONA_PRESETS["economy"]
            assert state.persona_preset == "economy"
            event = await _next_event(ws)
            assert event["type"] == "status"
            assert event["payload"]["persona_preset"] == "economy"

            reply = await _rpc(ws, "preset/apply", {"preset": "chaos"})
            assert reply["type"] == "error"
            assert "unknown preset" in reply["payload"]["message"]

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_budget_set_ok_and_validation(addon) -> None:
    """budget/set sets the daily budget; non-number is an error."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "budget/set", {"value": 42.5})
            assert reply == {"type": "budget/set", "payload": {"ok": True}}
            assert state.daily_budget == 42.5
            event = await _next_event(ws)
            assert event["type"] == "status"
            assert event["payload"]["daily_budget"] == 42.5

            reply = await _rpc(ws, "budget/set", {"value": "lots"})
            assert reply["type"] == "error"
            assert "number" in reply["payload"]["message"]

            reply = await _rpc(ws, "budget/set", {})
            assert reply["type"] == "error"

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_mode_set_ok_and_validation(addon) -> None:
    """mode/set switches the home mode; unknown mode is an error."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "mode/set", {"mode": "vacation"})
            assert reply == {"type": "mode/set", "payload": {"ok": True}}
            assert state.mode == "vacation"
            event = await _next_event(ws)
            assert event["type"] == "status"
            assert event["payload"]["mode"] == "vacation"

            reply = await _rpc(ws, "mode/set", {"mode": "apocalypse"})
            assert reply["type"] == "error"
            assert "unknown mode" in reply["payload"]["message"]

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_setter_broadcasts_status_to_other_clients(addon) -> None:
    """A successful preset/apply pushes a fresh status event to every client."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as observer:
            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as actor:
                await _rpc(actor, "preset/apply", {"preset": "observer"})
                # The broadcast reaches the other connection...
                event = await _next_event(observer)
                assert event["type"] == "status"
                assert event["payload"]["persona_preset"] == "observer"
                assert event["payload"]["persona"] == PERSONA_PRESETS["observer"]
                # ...and the acting connection too, right after its ack.
                own = await _next_event(actor)
                assert own["type"] == "status"
                assert own["payload"]["persona_preset"] == "observer"


async def test_config_get_without_file(addon) -> None:
    """No config file -> configured:false with empty sections."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "config/get", {})
            assert reply["type"] == "config/get"
            assert reply["payload"] == {"configured": False, "sections": {}}


async def test_config_get_with_broken_json(addon, tmp_path) -> None:
    """An unparsable config file degrades to configured:false, no crash."""
    (tmp_path / "pilot.json").write_text("{not valid json", encoding="utf-8")
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "config/get", {})
            assert reply["payload"] == {"configured": False, "sections": {}}


async def test_config_get_masks_secrets(addon, tmp_path) -> None:
    """Secrets are masked at any depth; keys stay; plain values pass through."""
    secret_config = {
        "channels": {
            "max": {"token": "abc123", "enabled": True},
            "telegram": {"chat_id": 4242},
        },
        "models": {
            "providers": [{"provider": "anthropic", "api_key": "sk-9"}],
            "password": "hunter2",
            "license_key": "LK-42",
        },
        "plugins": {"catalog": ["night-lights", "guest-mode"]},
    }
    (tmp_path / "pilot.json").write_text(json.dumps(secret_config), encoding="utf-8")
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "config/get", {})
            payload = reply["payload"]
            assert payload["configured"] is True

            sections = payload["sections"]
            # Keys are never dropped — the UI shows "configured".
            assert sections["channels"]["max"]["token"] == "***"
            assert sections["models"]["password"] == "***"
            assert sections["models"]["license_key"] == "***"
            assert sections["models"]["providers"][0]["api_key"] == "***"
            # Non-secret values pass through untouched.
            assert sections["channels"]["max"]["enabled"] is True
            assert sections["channels"]["telegram"]["chat_id"] == 4242
            assert sections["models"]["providers"][0]["provider"] == "anthropic"
            assert sections["plugins"]["catalog"] == ["night-lights", "guest-mode"]

            # No real secret value anywhere in the wire payload.
            wire = json.dumps(payload)
            for secret in ("abc123", "sk-9", "hunter2", "LK-42"):
                assert secret not in wire
