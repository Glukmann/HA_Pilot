"""Tests for the WS config/set command and the onboarded snapshot flag."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json

import aiohttp
from aiohttp import WSMsgType, web
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state
from pilot_addon.state import RuntimeState
import pytest

SECRET_VALUE = "sk-supersecret-123"


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


async def test_set_creates_file_and_sections_coexist(addon, tmp_path) -> None:
    """A fresh file is created; a second section does not clobber the first."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(
                ws,
                "config/set",
                {"section": "channels", "values": {"max": {"enabled": True}}},
            )
            assert reply == {
                "type": "config/set",
                "payload": {"ok": True, "section": "channels"},
            }
            assert (tmp_path / "pilot.json").is_file()
            assert json.loads((tmp_path / "pilot.json").read_text()) == {
                "channels": {"max": {"enabled": True}}
            }
            # A successful set broadcasts status to the acting connection too.
            await _next_event(ws)

            reply = await _rpc(
                ws,
                "config/set",
                {
                    "section": "supervisor",
                    "values": {
                        "base_url": "https://x/v1",
                        "api_key": "k",
                        "model": "m",
                    },
                },
            )
            assert reply["payload"]["ok"] is True
            saved = json.loads((tmp_path / "pilot.json").read_text())
            assert saved["channels"] == {"max": {"enabled": True}}
            assert saved["supervisor"]["model"] == "m"


async def test_set_deep_merges_preserving_untouched_keys(addon, tmp_path) -> None:
    """Re-setting a section keeps keys the caller did not mention."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await _rpc(
                ws,
                "config/set",
                {
                    "section": "supervisor",
                    "values": {
                        "base_url": "https://x/v1",
                        "api_key": SECRET_VALUE,
                        "schedule": "08:30",
                    },
                },
            )
            await _next_event(ws)  # status broadcast
            reply = await _rpc(
                ws, "config/set", {"section": "supervisor", "values": {"model": "m"}}
            )
            assert reply["payload"] == {"ok": True, "section": "supervisor"}
            section = json.loads((tmp_path / "pilot.json").read_text())["supervisor"]
            assert section["base_url"] == "https://x/v1"
            assert section["api_key"] == SECRET_VALUE
            assert section["schedule"] == "08:30"
            assert section["model"] == "m"


async def test_snapshot_onboarded_flag_follows_supervisor_config(addon) -> None:
    """onboarded:false until base_url+api_key+model exist, then true."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "status", {})
            assert reply["payload"]["onboarded"] is False

            # Partial section does not count.
            await _rpc(
                ws, "config/set", {"section": "supervisor", "values": {"model": "m"}}
            )
            await _next_event(ws)  # status broadcast
            reply = await _rpc(ws, "status", {})
            assert reply["payload"]["onboarded"] is False

            await _rpc(
                ws,
                "config/set",
                {
                    "section": "supervisor",
                    "values": {"base_url": "https://x/v1", "api_key": "k"},
                },
            )
            await _next_event(ws)  # status broadcast
            reply = await _rpc(ws, "status", {})
            assert reply["payload"]["onboarded"] is True
            assert state.is_onboarded() is True


async def test_set_recovers_from_broken_json(addon, tmp_path) -> None:
    """A broken config is backed up next to the fresh write, not silently lost."""
    state, port = addon
    state.config_path.write_text("{not json at all", encoding="utf-8")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(
                ws,
                "config/set",
                {"section": "channels", "values": {"max": {"enabled": True}}},
            )
            assert reply["payload"]["ok"] is True
    backups = list(tmp_path.glob("pilot.json.bad-*"))
    assert len(backups) == 1
    assert "{not json at all" in backups[0].read_text(encoding="utf-8")
    assert json.loads((tmp_path / "pilot.json").read_text()) == {
        "channels": {"max": {"enabled": True}}
    }


async def test_audit_and_logs_never_carry_values(addon, tmp_path) -> None:
    """The audit record and the log stream mention only the section name."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await _rpc(
                ws,
                "config/set",
                {"section": "supervisor", "values": {"api_key": SECRET_VALUE}},
            )
            await _next_event(ws)  # status broadcast
            reply = await _rpc(ws, "logs/recent", {})
            log_text = json.dumps(reply["payload"], ensure_ascii=False)
            assert SECRET_VALUE not in log_text
    audit_text = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"event": "config.set"' in audit_text
    assert '"section": "supervisor"' in audit_text
    assert SECRET_VALUE not in audit_text


async def test_set_broadcasts_status_to_other_clients(addon) -> None:
    """A successful config/set pushes a fresh status event to every client."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as observer:
            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as actor:
                await _rpc(
                    actor,
                    "config/set",
                    {
                        "section": "supervisor",
                        "values": {
                            "base_url": "https://x/v1",
                            "api_key": "k",
                            "model": "m",
                        },
                    },
                )
                event = await _next_event(observer)
                assert event["type"] == "status"
                assert event["payload"]["onboarded"] is True
                own = await _next_event(actor)
                assert own["type"] == "status"


async def test_set_validation_errors_keep_connection(addon) -> None:
    """Bad payloads answer an error frame; nothing is written."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(
                ws,
                "config/set",
                {"section": "supervisor", "values": ["not", "a", "dict"]},
            )
            assert reply["type"] == "error"
            assert "values must be an object" in reply["payload"]["message"]

            reply = await _rpc(ws, "config/set", {"values": {"a": 1}})
            assert reply["type"] == "error"
            assert "section" in reply["payload"]["message"]

            assert not state.config_path.exists()

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"
