"""Tests for model profiles: ws commands, key masking, active-model resolution."""

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
from pilot_addon.modelstore import ModelError, resolve_section, upsert
from pilot_addon.state import RuntimeState
from pilot_addon.supervisor import run_supervisor
import pytest

# -- unit: modelstore ----------------------------------------------------------


def test_upsert_creates_profiles_with_unique_ids():
    section: dict = {}
    models, first = upsert(
        section,
        {
            "label": "DeepSeek",
            "base_url": "https://a/v1",
            "api_key": "k1",
            "model": "m1",
        },
    )
    assert first == "deepseek"
    models, second = upsert(
        {**section, "models": models},
        {
            "label": "DeepSeek",
            "base_url": "https://b/v1",
            "api_key": "k2",
            "model": "m2",
        },
    )
    assert second == "deepseek-2"
    assert len(models) == 2


def test_upsert_mask_keeps_stored_key():
    models, _ = upsert(
        {},
        {"label": "A", "base_url": "https://a/v1", "api_key": "real-key", "model": "m"},
    )
    models, pid = upsert(
        {"models": models},
        {
            "id": pid if (pid := models[0]["id"]) else "",
            "label": "A",
            "base_url": "https://a/v1",
            "api_key": "***",
            "model": "m2",
        },
    )
    assert models[0]["api_key"] == "real-key"
    assert models[0]["model"] == "m2"


def test_upsert_mask_without_stored_key_rejected():
    with pytest.raises(ModelError):
        upsert(
            {},
            {"label": "A", "base_url": "https://a/v1", "api_key": "***", "model": "m"},
        )


def test_resolve_active_profile_overrides_legacy():
    section = {
        "base_url": "https://legacy/v1",
        "api_key": "legacy-key",
        "model": "legacy-model",
        "schedule": "07:00",
        "models": [
            {
                "id": "b",
                "base_url": "https://b/v1",
                "api_key": "key-b",
                "model": "model-b",
                "price_input_per_1m": 10.0,
            }
        ],
        "active_id": "b",
    }
    resolved = resolve_section(section)
    assert resolved["base_url"] == "https://b/v1"
    assert resolved["api_key"] == "key-b"
    assert resolved["model"] == "model-b"
    assert resolved["price_input_per_1m"] == 10.0
    assert resolved["schedule"] == "07:00"  # untouched by the profile
    # No profiles / dangling active_id -> legacy as-is.
    assert resolve_section(section)["active_id"] == "b"
    legacy_only = {k: v for k, v in section.items() if k not in ("models", "active_id")}
    assert resolve_section(legacy_only)["base_url"] == "https://legacy/v1"
    dangling = {**legacy_only, "active_id": "ghost"}
    assert resolve_section(dangling)["base_url"] == "https://legacy/v1"


# -- ws commands ----------------------------------------------------------------


@pytest.fixture
async def addon(tmp_path, socket_enabled) -> AsyncIterator[tuple[RuntimeState, int]]:
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
    await ws.send_str(json.dumps({"type": cmd, "payload": payload}))
    # Mutating commands broadcast a status event to every connection,
    # including the caller — skip events until the reply to this command.
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=5)
        assert msg.type == WSMsgType.TEXT, msg
        frame = json.loads(msg.data)
        if frame["type"] in ("status", "queue", "log"):
            continue
        return frame


_PROFILE = {
    "label": "DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-test",
    "model": "deepseek-chat",
    "price_input_per_1m": 15,
    "price_output_per_1m": 60,
}


async def test_models_lifecycle_over_ws(addon) -> None:
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "models/list", {})
            assert reply["payload"] == {"items": [], "active_id": None}

            reply = await _rpc(ws, "models/upsert", dict(_PROFILE))
            assert reply["payload"]["ok"] is True
            model_id = reply["payload"]["id"]

            reply = await _rpc(ws, "models/list", {})
            items = reply["payload"]["items"]
            assert len(items) == 1
            # The key never leaves the add-on; has_key stands in for it.
            assert "api_key" not in items[0]
            assert items[0]["has_key"] is True
            assert items[0]["label"] == "DeepSeek"

            reply = await _rpc(ws, "models/activate", {"id": model_id})
            assert reply["payload"]["active_id"] == model_id

            # The active profile cannot be removed.
            reply = await _rpc(ws, "models/remove", {"id": model_id})
            assert reply["type"] == "error"

            # A second profile: add, then remove the inactive one.
            other = dict(_PROFILE, label="OpenAI", base_url="https://api.openai.com/v1")
            reply = await _rpc(ws, "models/upsert", other)
            other_id = reply["payload"]["id"]
            reply = await _rpc(ws, "models/remove", {"id": other_id})
            assert reply["payload"] == {"ok": True}

            reply = await _rpc(ws, "models/activate", {"id": "ghost"})
            assert reply["type"] == "error"

    # Onboarded via the active profile (base config has no legacy fields).
    assert state.is_onboarded() is True
    saved = json.loads(state.config_path.read_text(encoding="utf-8"))
    assert saved["supervisor"]["active_id"] == model_id
    assert saved["supervisor"]["models"][0]["api_key"] == "sk-test"


async def test_models_upsert_validation_errors(addon) -> None:
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(
                ws, "models/upsert", dict(_PROFILE, base_url="ftp://nope")
            )
            assert reply["type"] == "error"
            assert "http" in reply["payload"]["message"]
            reply = await _rpc(ws, "models/upsert", dict(_PROFILE, model=""))
            assert reply["type"] == "error"


# -- supervisor run uses the active profile --------------------------------------


@pytest.fixture
async def provider(socket_enabled):
    """Fake LLM provider serving GET /v1/models."""
    requests: list[tuple[str, dict]] = []
    status = 200
    payload: dict = {
        "data": [
            {"id": "deepseek-flash"},
            {"id": "deepseek-pro"},
            {"id": "deepseek-pro", "created": 1},
        ]
    }

    async def _models(request: web.Request) -> web.Response:
        requests.append((request.path, dict(request.headers)))
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_get("/v1/models", _models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    class FakeProvider:
        def __init__(self) -> None:
            self.requests = requests

        @property
        def url(self) -> str:
            return f"http://127.0.0.1:{port}/v1"

        def fail(self, code: int) -> None:
            nonlocal status
            status = code

        def broken(self) -> None:
            nonlocal payload
            payload = {"unexpected": True}

    yield FakeProvider()
    await runner.cleanup()


async def _discover(ws: aiohttp.ClientWebSocketResponse, payload: dict) -> dict:
    return await _rpc(ws, "models/discover", payload)


async def test_models_discover_lists_and_dedupes(addon, provider) -> None:
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _discover(
                ws, {"base_url": provider.url, "api_key": "k"}
            )
            assert reply["payload"] == {
                "models": ["deepseek-flash", "deepseek-pro"]
            }
    auth = provider.requests[0][1].get("Authorization")
    assert auth == "Bearer k"


async def test_models_discover_uses_stored_key_for_existing_profile(
    addon, provider
) -> None:
    state, port = addon
    models, profile_id = upsert(
        {},
        {
            "label": "Fake",
            "base_url": provider.url,
            "api_key": "stored-key",
            "model": "m",
        },
    )
    state.write_config_section("supervisor", {"models": models})
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _discover(
                ws, {"base_url": provider.url, "api_key": "***", "id": profile_id}
            )
            assert reply["payload"]["models"] == ["deepseek-flash", "deepseek-pro"]
    assert provider.requests[0][1].get("Authorization") == "Bearer stored-key"


async def test_models_discover_error_paths(addon, provider) -> None:
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            # No key, no stored profile.
            reply = await _discover(ws, {"base_url": provider.url})
            assert reply["type"] == "error"
            # Bad base_url.
            reply = await _discover(ws, {"base_url": "ftp://x", "api_key": "k"})
            assert reply["type"] == "error"
            # Provider HTTP error surfaces without the key anywhere.
            provider.fail(401)
            reply = await _discover(
                ws, {"base_url": provider.url, "api_key": "secret-key"}
            )
            assert reply["type"] == "error"
            assert "401" in reply["payload"]["message"]
            # Broken payload shape.
            provider.fail(200)
            provider.broken()
            reply = await _discover(
                ws, {"base_url": provider.url, "api_key": "k"}
            )
            assert reply["type"] == "error"


@pytest.fixture
async def env(tmp_path, socket_enabled, monkeypatch):
    """Fake LLM/HA backend; supervisor config points at the fake endpoints."""
    state = build_state(tmp_path)
    checker = Checker()
    state.attach_checker(checker)
    requests: list[tuple[str, dict, dict]] = []

    async def _llm(request: web.Request) -> web.Response:
        body = await request.json()
        requests.append(("/v1/chat/completions", body, dict(request.headers)))
        return web.json_response(
            {
                "choices": [
                    {"message": {"content": '{"summary": "ok", "decisions": []}'}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
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
    yield state, checker, requests, port
    await runner.cleanup()


async def test_supervisor_run_uses_active_profile(env) -> None:
    state, checker, requests, port = env
    state.vitrine.update(
        {"sensor.temp": {"state": "unknown", "attrs": {}, "last_changed": 0}}
    )
    await run_checker_pass(state, checker)  # fresh flags: sensor_dead
    state.flags = ["sensor_dead:sensor.temp"]

    models, profile_id = upsert(
        {},
        {
            "label": "Fake",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "profile-key",
            "model": "profile-model",
        },
    )
    state.write_config_section(
        "supervisor",
        {"schedule": "07:00", "models": models, "active_id": profile_id},
    )

    result = await run_supervisor(state)
    assert result["ok"] is True
    assert len(requests) == 1
    _path, body, headers = requests[0]
    assert body["model"] == "profile-model"
    assert headers["Authorization"] == "Bearer profile-key"
