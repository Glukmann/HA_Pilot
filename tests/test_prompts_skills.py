"""Tests for the editable prompt/skill registry and the workshop WS commands."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time

import aiohttp
from aiohttp import WSMsgType, web
from pilot_addon.checker import Checker
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state, run_checker_pass
from pilot_addon.promptstore import seed_defaults
from pilot_addon.state import RuntimeState
from pilot_addon.supervisor import run_supervisor
import pytest

CUSTOM_PROMPT = "Ты — тестовый супервизор. Всегда предлагай чай."
CUSTOM_SKILL = "ПРАВИЛО_ЧАЯ_777: перед любым решением предлагать чай."
CUSTOM_CONTENT = "УНИКАЛЬНЫЙ_КОНТЕНТ_31337"


@pytest.fixture
async def addon(tmp_path, socket_enabled) -> AsyncIterator[tuple[RuntimeState, int]]:
    """Seeded add-on app on an ephemeral port (same pattern as test_addon_ws)."""
    state = build_state(tmp_path)
    seed_defaults(tmp_path)
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


def test_seed_creates_defaults_and_prompt_is_used(tmp_path) -> None:
    """A fresh data dir gets the bundle; the supervisor reads the seeded prompt."""
    state = build_state(tmp_path)
    assert not (tmp_path / "prompts" / "supervisor.md").exists()
    seed_defaults(tmp_path)
    prompt = (tmp_path / "prompts" / "supervisor.md").read_text(encoding="utf-8")
    assert "супервизор умного дома" in prompt
    assert (tmp_path / "skills" / "supervisor-home" / "SKILL.md").is_file()
    assert (tmp_path / "skills" / "home-domain" / "SKILL.md").is_file()

    from pilot_addon.supervisor import SYSTEM_PROMPT, _system_prompt

    assert _system_prompt(state) == prompt.strip()
    # Unseeded dir falls back to the bundled constant.
    empty = build_state(tmp_path / "elsewhere")
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    assert _system_prompt(empty) == SYSTEM_PROMPT


async def test_prompt_and_skill_roundtrip(addon) -> None:
    """set -> get returns the edit; reset restores the bundled default."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(
                ws, "prompts/set", {"name": "supervisor", "content": CUSTOM_PROMPT}
            )
            assert reply["payload"] == {"ok": True, "name": "supervisor"}
            await _next_event(ws)  # status broadcast

            reply = await _rpc(ws, "prompts/get", {"name": "supervisor"})
            assert reply["payload"]["content"] == CUSTOM_PROMPT

            reply = await _rpc(ws, "prompts/reset", {"name": "supervisor"})
            assert reply["payload"]["ok"] is True
            reply = await _rpc(ws, "prompts/get", {"name": "supervisor"})
            assert "супервизор умного дома" in reply["payload"]["content"]
            assert CUSTOM_PROMPT not in reply["payload"]["content"]

            reply = await _rpc(
                ws,
                "skills/set",
                {
                    "name": "home-domain",
                    "content": (
                        f"---\nname: home-domain\ndescription: Доменные знания теста\n"
                        f"---\n{CUSTOM_SKILL}"
                    ),
                },
            )
            assert reply["payload"]["ok"] is True
            await _next_event(ws)  # status broadcast

            reply = await _rpc(ws, "skills/get", {"name": "home-domain"})
            assert CUSTOM_SKILL in reply["payload"]["content"]
            assert reply["payload"]["description"] == "Доменные знания теста"

            reply = await _rpc(ws, "skills/reset", {"name": "home-domain"})
            assert reply["payload"]["ok"] is True
            reply = await _rpc(ws, "skills/get", {"name": "home-domain"})
            assert "<climate entity_id теплового насоса>" in reply["payload"]["content"]


async def test_name_traversal_and_slash_rejected(addon, tmp_path) -> None:
    """Path-looking names fail cleanly and never touch the filesystem."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            for bad in ("../evil", "a/b", "..", "UPPER"):
                reply = await _rpc(ws, "prompts/set", {"name": bad, "content": "x"})
                assert reply["type"] == "error", bad
                reply = await _rpc(ws, "prompts/get", {"name": bad})
                assert reply["type"] == "error", bad
    assert list(tmp_path.rglob("*evil*")) == []
    assert not (tmp_path / "a").exists()

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "prompts/get", {"name": "no-such-prompt"})
            assert reply["type"] == "error"
            reply = await _rpc(ws, "prompts/reset", {"name": "no-such-prompt"})
            assert reply["type"] == "error"
            reply = await _rpc(ws, "skills/reset", {"name": "custom-only"})
            assert reply["type"] == "error"


def test_reseed_preserves_edits_and_restores_missing(tmp_path) -> None:
    """Startup seeding is additive: edits survive, deleted defaults return."""
    build_state(tmp_path)
    seed_defaults(tmp_path)
    prompt_path = tmp_path / "prompts" / "supervisor.md"
    prompt_path.write_text(CUSTOM_PROMPT, encoding="utf-8")
    (tmp_path / "skills" / "home-domain" / "SKILL.md").unlink()

    seed_defaults(tmp_path)

    assert prompt_path.read_text(encoding="utf-8") == CUSTOM_PROMPT
    assert (tmp_path / "skills" / "home-domain" / "SKILL.md").is_file()


class _FakeLlm:
    """Records chat/completions payloads on a real ephemeral aiohttp server."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def handler(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        return web.json_response(
            {
                "choices": [
                    {"message": {"content": '{"summary": "ok", "decisions": []}'}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )


@pytest.fixture
async def llm_server(socket_enabled) -> AsyncIterator[_FakeLlm]:
    fake = _FakeLlm()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.port = site._server.sockets[0].getsockname()[1]
    yield fake
    await runner.cleanup()


async def test_supervisor_uses_edited_prompt_and_skills(
    addon, llm_server, monkeypatch
) -> None:
    """The LLM receives the edited system prompt and the runtime skills block."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await _rpc(
                ws, "prompts/set", {"name": "supervisor", "content": CUSTOM_PROMPT}
            )
            await _next_event(ws)
            await _rpc(
                ws,
                "skills/set",
                {"name": "home-domain", "content": CUSTOM_SKILL},
            )
            await _next_event(ws)

    state.config_path.write_text(
        json.dumps(
            {
                "supervisor": {
                    "base_url": f"http://127.0.0.1:{llm_server.port}/v1",
                    "api_key": "k",
                    "model": "m",
                }
            }
        ),
        encoding="utf-8",
    )
    checker = Checker()
    state.attach_checker(checker)
    state.vitrine.update(
        {
            "sensor.temp": {
                "state": "unknown",
                "attrs": {},
                "last_changed": time.time() - 7 * 3600,
                "area": "T",
            }
        }
    )
    await run_checker_pass(state, checker)

    result = await run_supervisor(state)
    assert result["ok"] is True
    assert llm_server.requests, "LLM was not called"
    messages = llm_server.requests[0]["messages"]
    assert messages[0]["content"] == CUSTOM_PROMPT
    assert "Навыки рантайма" in messages[1]["content"]
    assert CUSTOM_SKILL in messages[1]["content"]
    assert "Флаги отклонений" in messages[1]["content"]  # data follows the skills


async def test_audit_carries_names_only(addon, tmp_path) -> None:
    """prompt.set/skill.set audit records never contain the content."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await _rpc(
                ws, "prompts/set", {"name": "supervisor", "content": CUSTOM_CONTENT}
            )
            await _next_event(ws)
            await _rpc(
                ws, "skills/set", {"name": "home-domain", "content": CUSTOM_CONTENT}
            )
            await _next_event(ws)
    audit_text = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"prompt.set"' in audit_text
    assert '"skill.set"' in audit_text
    assert CUSTOM_CONTENT not in audit_text


async def test_lists_report_defaults_and_descriptions(addon) -> None:
    """prompts/list flags fresh defaults; skills/list parses descriptions."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            reply = await _rpc(ws, "prompts/list", {})
            prompts = {p["name"]: p for p in reply["payload"]["prompts"]}
            assert prompts["supervisor"]["is_default"] is True
            assert prompts["supervisor"]["size"] > 0
            assert prompts["supervisor"]["modified_ts"] is not None

            reply = await _rpc(ws, "skills/list", {})
            skills = {s["name"]: s for s in reply["payload"]["skills"]}
            assert set(skills) == {"supervisor-home", "home-domain"}
            assert "description" in skills["supervisor-home"]

            await _rpc(
                ws, "prompts/set", {"name": "supervisor", "content": CUSTOM_PROMPT}
            )
            await _next_event(ws)
            reply = await _rpc(ws, "prompts/list", {})
            prompts = {p["name"]: p for p in reply["payload"]["prompts"]}
            assert prompts["supervisor"]["is_default"] is False
