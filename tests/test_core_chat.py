"""Chat turns now go through the bundled core agent (OpenAI endpoint)."""

from __future__ import annotations

import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pilot_addon.corebridge as bridge
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state


def _with_profile(tmp_path) -> None:
    (tmp_path / "pilot.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "supervisor": {
                    "base_url": "https://llm.example/v1",
                    "api_key": "k",
                    "model": "deepseek-chat",
                    "price_input_per_1m": 2.0,
                    "price_output_per_1m": 6.0,
                },
            }
        ),
        encoding="utf-8",
    )


async def _start(tmp_path, socket_enabled, monkeypatch, core_status=200):
    calls = []

    async def chat(request: web.Request) -> web.Response:
        calls.append(await request.json())
        if core_status != 200:
            return web.Response(status=core_status)
        return web.json_response(
            {
                "choices": [{"message": {"content": "Вижу дом, всё штатно."}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 200},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(
        bridge, "CHAT_URL", f"{server.make_url('')}/v1/chat/completions"
    )
    state = build_state(tmp_path)
    addon_app = create_app(state)
    runner = web.AppRunner(addon_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return state, calls, port, runner, server


async def _post_chat(port, message):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/api/chat", json={"message": message}
        ) as resp:
            return resp.status, await resp.json()


async def test_chat_via_core(tmp_path, socket_enabled, monkeypatch):
    _with_profile(tmp_path)
    state, calls, port, runner, server = await _start(
        tmp_path, socket_enabled, monkeypatch
    )
    try:
        status, body = await _post_chat(port, "что видишь в доме?")
        assert status == 200
        assert body["say"] == "Вижу дом, всё штатно."
        assert body["actions"] == []
        assert body["via"] == "core"
        assert calls and calls[0]["messages"][0]["content"] == "что видишь в доме?"
        assert state.cost_today > 0
    finally:
        await runner.cleanup()
        await server.close()


async def test_chat_core_down_soft_reply(tmp_path, socket_enabled, monkeypatch):
    _with_profile(tmp_path)
    _state, _calls, port, runner, server = await _start(
        tmp_path, socket_enabled, monkeypatch, core_status=503
    )
    try:
        status, body = await _post_chat(port, "привет")
        assert status == 200
        assert body["say"]
        assert body["error"]
    finally:
        await runner.cleanup()
        await server.close()


async def test_chat_not_configured(tmp_path, socket_enabled, monkeypatch):
    _state, calls, port, runner, server = await _start(
        tmp_path, socket_enabled, monkeypatch
    )
    try:
        status, body = await _post_chat(port, "привет")
        assert status == 200
        assert "не настроен" in body["say"]
        assert calls == []  # без профиля до ядра не дошло
    finally:
        await runner.cleanup()
        await server.close()
