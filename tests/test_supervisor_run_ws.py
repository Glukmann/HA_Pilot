"""Cycle 1.6: the supervisor/run ws command (run the daily LLM run now)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json

import aiohttp
from aiohttp import WSMsgType, web
from pilot_addon import supervisor
from pilot_addon.checker import Checker
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state, run_checker_pass
from pilot_addon.state import RuntimeState
import pytest


@pytest.fixture
async def addon(
    tmp_path, socket_enabled, monkeypatch
) -> AsyncIterator[tuple[RuntimeState, int]]:
    state = build_state(tmp_path)
    checker = Checker()
    state.attach_checker(checker)
    state.vitrine.update(
        {"sensor.x": {"state": "unknown", "attrs": {}, "last_changed": 0}}
    )
    await run_checker_pass(state, checker)
    state.flags = ["sensor_dead:sensor.x"]

    async def _llm(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "choices": [
                    {"message": {"content": '{"summary": "ok", "decisions": []}'}}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", _llm)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(supervisor, "HA_API_BASE", f"http://127.0.0.1:{port}/api")
    state.write_config_section(
        "supervisor",
        {"base_url": f"http://127.0.0.1:{port}/v1", "api_key": "k", "model": "m"},
    )

    addon_app = create_app(state)
    addon_runner = web.AppRunner(addon_app)
    await addon_runner.setup()
    addon_site = web.TCPSite(addon_runner, "127.0.0.1", 0)
    await addon_site.start()
    addon_port = addon_site._server.sockets[0].getsockname()[1]
    yield state, addon_port
    await addon_runner.cleanup()
    await runner.cleanup()


async def _rpc(ws: aiohttp.ClientWebSocketResponse, cmd: str, payload: dict) -> dict:
    await ws.send_str(json.dumps({"type": cmd, "payload": payload}))
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=10)
        assert msg.type == WSMsgType.TEXT, msg
        frame = json.loads(msg.data)
        if frame["type"] in ("status", "queue", "log"):
            continue
        return frame


async def test_supervisor_run_starts_background_task(addon) -> None:
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "supervisor/run", {})
            assert reply["payload"] == {"ok": True, "started": True}
            # Пока run идёт — второй запуск отклоняется.
            busy = await _rpc(ws, "supervisor/run", {})
            assert busy["type"] == "error"
            assert "уже выполняется" in busy["payload"]["message"]

    for _ in range(50):
        if state.supervisor_status.get("last_run_ts") and not state.supervisor_busy:
            break
        await asyncio.sleep(0.1)
    assert state.supervisor_status["last_run_ts"] is not None
    text = (state.queue._audit.path).read_text(encoding="utf-8")
    assert '"event": "supervisor.run"' in text
    assert state.supervisor_busy is False
