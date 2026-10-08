"""The core's only door into the home: POST /api/action through safety."""

from __future__ import annotations

import aiohttp
from aiohttp import web
from pilot_addon.executor import ExecResult
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state


class _FakeExecutor:
    """Records actions; 'fail' in entity_id simulates an HA error."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def apply(self, action):
        self.calls.append(action)
        if "fail" in str(action.get("entity_id")):
            raise RuntimeError("ha down")
        return ExecResult(
            status="applied", detail={"entity_id": action.get("entity_id")}
        )


async def _start(tmp_path, socket_enabled):
    state = build_state(tmp_path)
    executor = _FakeExecutor()
    app = create_app(state, executor=executor)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return state, executor, port, runner


async def _post(port, payload):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/api/action", json=payload
        ) as resp:
            return resp.status, await resp.json()


async def test_action_direct_applies(tmp_path, socket_enabled):
    state, executor, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post(
            port,
            {"domain": "light", "service": "turn_on", "entity_id": "light.kabinet"},
        )
        assert status == 200
        assert body["status"] == "applied"
        assert executor.calls and executor.calls[0]["entity_id"] == "light.kabinet"
        assert state.queue_size == 0  # direct — без очереди
    finally:
        await runner.cleanup()


async def test_action_refuse_never_executes(tmp_path, socket_enabled):
    state, executor, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post(
            port,
            {"domain": "camera", "service": "turn_on", "entity_id": "camera.porch"},
        )
        assert status == 200
        assert body["status"] == "refused"
        assert executor.calls == []
        assert state.queue_size == 0
    finally:
        await runner.cleanup()


async def test_action_queue_for_non_whitelisted(tmp_path, socket_enabled):
    state, executor, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post(
            port, {"domain": "switch", "service": "turn_on", "entity_id": "switch.gate"}
        )
        assert status == 200
        assert body["status"] == "queued"
        assert state.queue_size == 1
        assert executor.calls == []  # ждёт подтверждения хозяина
    finally:
        await runner.cleanup()


async def test_action_ha_error_requeues(tmp_path, socket_enabled):
    state, _executor, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post(
            port, {"domain": "light", "service": "turn_on", "entity_id": "light.fail"}
        )
        assert status == 200
        assert body["status"] == "queued"
        assert state.queue_size == 1
    finally:
        await runner.cleanup()


async def test_action_validation(tmp_path, socket_enabled):
    _state, _executor, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post(port, {"domain": "light"})
        assert status == 400
        assert body["status"] == "refused"
    finally:
        await runner.cleanup()
