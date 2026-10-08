"""Tests for the core bridge: chat through /v1/chat/completions."""

from __future__ import annotations

import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pilot_addon.corebridge as bridge
from pilot_addon.corebridge import ask_core
from pilot_addon.main import build_state

PROFILE = {
    "base_url": "https://llm.example/v1",
    "api_key": "k",
    "model": "deepseek-chat",
    "price_input_per_1m": 2.0,
    "price_output_per_1m": 6.0,
}


def _with_profile(tmp_path) -> None:
    (tmp_path / "pilot.json").write_text(
        json.dumps({"schema_version": 3, "supervisor": dict(PROFILE)}),
        encoding="utf-8",
    )


async def test_ask_core_success_counts_cost(tmp_path, socket_enabled, monkeypatch):
    _with_profile(tmp_path)
    seen = {}

    async def chat(request: web.Request) -> web.Response:
        seen["session"] = request.headers.get("X-OpenClaw-Session")
        return web.json_response(
            {
                "choices": [{"message": {"content": "Привет, всё в порядке."}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app)
    await server.start_server()
    state = build_state(tmp_path)
    try:
        monkeypatch.setattr(
            bridge, "CHAT_URL", f"{server.make_url('')}/v1/chat/completions"
        )
        async with aiohttp.ClientSession() as session:
            result = await ask_core(state, "как дела?", "c-1", session=session)
    finally:
        await server.close()
    assert result["ok"] is True
    assert result["reply"] == "Привет, всё в порядке."
    assert seen["session"] == "pilot:c-1"
    # 1000*2/1e6 + 500*6/1e6 = 0.005
    assert abs(result["cost"] - 0.005) < 1e-9
    assert abs(state.cost_today - 0.005) < 1e-9


async def test_ask_core_budget_gate_skips_call(tmp_path, socket_enabled, monkeypatch):
    _with_profile(tmp_path)
    state = build_state(tmp_path)
    state.cost_today = state.daily_budget
    calls = []

    async def chat(request: web.Request) -> web.Response:
        calls.append(1)
        return web.json_response({"choices": []})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app)
    await server.start_server()
    try:
        monkeypatch.setattr(
            bridge, "CHAT_URL", f"{server.make_url('')}/v1/chat/completions"
        )
        async with aiohttp.ClientSession() as session:
            result = await ask_core(state, "привет", None, session=session)
    finally:
        await server.close()
    assert result["ok"] is False
    assert result["error"] == "budget"
    assert calls == []


async def test_ask_core_not_configured(tmp_path):
    state = build_state(tmp_path)
    result = await ask_core(state, "привет")
    assert result["ok"] is False
    assert result["error"] == "not_configured"


async def test_ask_core_down_is_soft(tmp_path, socket_enabled, monkeypatch):
    _with_profile(tmp_path)
    state = build_state(tmp_path)
    monkeypatch.setattr(bridge, "CHAT_URL", "http://127.0.0.1:1/v1/chat/completions")
    result = await ask_core(state, "привет")
    assert result["ok"] is False
    assert result["error"]
