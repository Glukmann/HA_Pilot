"""End-to-end tests of the add-on WebSocket protocol (/ws)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import logging

import aiohttp
from aiohttp import WSMsgType, web
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state
from pilot_addon.state import RuntimeState
import pytest

TEST_LOG = logging.getLogger("pilot.test")


@pytest.fixture
async def addon(tmp_path, socket_enabled) -> AsyncIterator[tuple[RuntimeState, int]]:
    """Run the add-on app on an ephemeral port (same pattern as REST tests)."""
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


async def test_ws_status_command(addon) -> None:
    """status returns the extended runtime snapshot (version, uptime, layers)."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"
            payload = reply["payload"]
            assert payload["runtime_version"] == state.runtime_version
            assert payload["daily_budget"] == state.daily_budget
            assert payload["queue_size"] == 0
            assert payload["cost_today"] == 0.0
            assert payload["uptime_s"] >= 0
            layers = payload["layers"]
            assert set(layers) == {"vitrine", "checker", "trust"}
            assert layers["vitrine"]["alive"] is False
            assert layers["trust"]["alive"] is True
            assert layers["trust"]["last_run_ts"] is None


async def test_ws_vitrine_get_command(addon) -> None:
    """vitrine/get mirrors the REST vitrine payload."""
    state, port = addon
    state.vitrine.update(
        {"light.office": {"state": "on", "attrs": {"friendly_name": "Office"}}}
    )
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "vitrine/get", {})
            assert reply["type"] == "vitrine/get"
            assert reply["payload"]["fresh"] is True
            assert "  Office: on" in reply["payload"]["lines"]


async def test_ws_queue_get_and_confirm(addon) -> None:
    """queue/get lists items; queue/confirm resolves them."""
    state, port = addon
    first = state.queue.propose("First?", {"type": "switch", "flag": "first"})
    second = state.queue.propose("Second?", {"type": "switch", "flag": "second"})
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "queue/get", {})
            assert [i["id"] for i in reply["payload"]["items"]] == [first, second]

            reply = await _rpc(ws, "queue/confirm", {"id": first, "decision": "yes"})
            assert reply == {"type": "queue/confirm", "payload": {"ok": True}}
            assert state.queue_size == 1

            reply = await _rpc(ws, "queue/get", {})
            assert [i["id"] for i in reply["payload"]["items"]] == [second]


async def test_ws_queue_confirm_unknown_is_error(addon) -> None:
    """Confirming a missing id answers an error frame, connection stays open."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "queue/confirm", {"id": "nope", "decision": "yes"})
            assert reply["type"] == "error"
            assert "not found" in reply["payload"]["message"]

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_ws_queue_confirm_broadcasts_queue_event(addon) -> None:
    """A successful confirm broadcasts a queue event to other connections."""
    state, port = addon
    item_id = state.queue.propose("Broadcast me?", {"type": "switch"})
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as observer:
            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as actor:
                await _rpc(actor, "queue/confirm", {"id": item_id, "decision": "no"})
                event = await _next_event(observer)
                assert event["type"] == "queue"
                assert event["payload"]["items"] == []


async def test_ws_logs_recent_returns_records(addon) -> None:
    """logs/recent sees records emitted through the logging root logger."""
    _state, port = addon
    TEST_LOG.warning("recent-marker-alpha")
    TEST_LOG.warning("recent-marker-beta")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "logs/recent", {})
            assert reply["type"] == "logs/recent"
            messages = [e["message"] for e in reply["payload"]]
            assert "recent-marker-alpha" in messages
            assert "recent-marker-beta" in messages
            entry = next(
                e for e in reply["payload"] if e["message"] == "recent-marker-alpha"
            )
            assert set(entry) == {"ts", "level", "logger", "message"}
            assert entry["logger"] == "pilot.test"
            assert entry["level"] == "WARNING"

            reply = await _rpc(ws, "logs/recent", {"limit": 1})
            assert len(reply["payload"]) == 1
            assert reply["payload"][0]["message"] == "recent-marker-beta"


async def test_ws_unknown_command_keeps_connection(addon) -> None:
    """An unregistered command answers an error frame; the socket stays open."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "vitrine/delete", {})
            assert reply["type"] == "error"
            assert "unknown command" in reply["payload"]["message"]

            reply = await _rpc(ws, "status", {})
            assert reply["type"] == "status"


async def test_ws_malformed_json_is_error(addon) -> None:
    """Non-JSON frames are rejected with an error frame."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await ws.send_str("{not json")
            event = await _next_event(ws)
            assert event["type"] == "error"
            assert "invalid JSON" in event["payload"]["message"]


async def test_ws_logs_subscribe_streams_and_unsubscribe_stops(addon) -> None:
    """Subscribed clients receive log events; unsubscribe stops the stream."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "logs/subscribe", {})
            assert reply == {"type": "logs/subscribe", "payload": {"ok": True}}

            TEST_LOG.warning("stream-marker-one")
            event = await _next_event(ws)
            assert event["type"] == "log"
            assert event["payload"]["message"] == "stream-marker-one"
            assert event["payload"]["logger"] == "pilot.test"

            reply = await _rpc(ws, "logs/unsubscribe", {})
            assert reply == {"type": "logs/unsubscribe", "payload": {"ok": True}}

            TEST_LOG.warning("stream-marker-two")
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(ws.receive(), timeout=0.5)


async def test_ws_logs_broadcast_to_multiple_subscribers(addon) -> None:
    """Every subscribed connection receives the same log event."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as first:
            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as second:
                await _rpc(first, "logs/subscribe", {})
                await _rpc(second, "logs/subscribe", {})
                TEST_LOG.warning("broadcast-marker")
                for ws in (first, second):
                    event = await _next_event(ws)
                    assert event["type"] == "log"
                    assert event["payload"]["message"] == "broadcast-marker"
