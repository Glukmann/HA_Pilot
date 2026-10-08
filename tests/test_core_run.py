"""Evening agent round (core automation webhook) + heartbeat cost estimate."""

from __future__ import annotations

import json

import aiohttp
from aiohttp import web
from pilot_addon import corebridge, coresync
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state


async def _start(tmp_path, socket_enabled):
    state = build_state(tmp_path)
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return state, port, runner


async def _post_core_run(port, payload):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/api/core-run", json=payload
        ) as resp:
            return resp.status, await resp.json()


async def test_core_run_with_summary_queues_a_note(tmp_path, socket_enabled):
    state, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, body = await _post_core_run(
            port,
            {
                "name": "pilot-round",
                "status": "ok",
                "summary": "Вечерний обход: всё штатно, в спальне прохладно.",
            },
        )
        assert status == 200
        assert body["ok"] is True
        assert state.queue_size == 1
        item = state.queue.items[0]
        assert "Вечерний обход Пилота" in item.title
        assert item.action.get("kind") == "proposal"
        audit = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        assert '"core.run"' in audit
    finally:
        await runner.cleanup()


async def test_core_run_empty_result_is_audit_only(tmp_path, socket_enabled):
    state, port, runner = await _start(tmp_path, socket_enabled)
    try:
        status, _body = await _post_core_run(
            port, {"name": "pilot-round", "status": "ok", "summary": ""}
        )
        assert status == 200
        assert state.queue_size == 0
    finally:
        await runner.cleanup()


async def test_core_run_bad_json_is_soft(tmp_path, socket_enabled):
    async with aiohttp.ClientSession() as session:
        _state, port, runner = await _start(tmp_path, socket_enabled)
        try:
            async with session.post(
                f"http://127.0.0.1:{port}/api/core-run", data="{broken"
            ) as resp:
                assert resp.status == 400
        finally:
            await runner.cleanup()


def _with_profile(tmp_path) -> None:
    (tmp_path / "pilot.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "supervisor": {
                    "base_url": "https://x/v1",
                    "api_key": "k",
                    "model": "m",
                    "price_input_per_1m": 2.0,
                    "price_output_per_1m": 6.0,
                },
            }
        ),
        encoding="utf-8",
    )


def test_heartbeat_estimate_accrues_once_per_day(tmp_path):
    _with_profile(tmp_path)
    state = build_state(tmp_path)
    state.core_alive = True
    assert corebridge.accrue_heartbeat_estimate(state) is True
    expected = 8 * 0.2
    assert abs(state.cost_today - expected) < 1e-9
    # Same day: no double accrual.
    assert corebridge.accrue_heartbeat_estimate(state) is False
    assert abs(state.cost_today - expected) < 1e-9
    # New day: accrues again (reset_cost_if_new_day runs first).
    state.heartbeat_estimated_day = "2000-01-01"
    assert corebridge.accrue_heartbeat_estimate(state) is True


def test_heartbeat_estimate_skips_when_core_down(tmp_path):
    _with_profile(tmp_path)
    state = build_state(tmp_path)
    state.core_alive = False
    assert corebridge.accrue_heartbeat_estimate(state) is False
    assert state.cost_today == 0.0


async def test_ensure_evening_round_idempotent(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def _fake_cli(args, timeout_s=20.0):
        calls.append(list(args))
        if args[:2] == ["automations", "list"]:
            return True, "pilot-round" if any(
                "pilot-round" in " ".join(c) for c in calls[:-1]
            ) else "other-job"
        return True, ""

    monkeypatch.setattr(corebridge, "core_cli", _fake_cli)
    assert await coresync.ensure_evening_round() is True
    assert await coresync.ensure_evening_round() is True
    adds = [c for c in calls if c[:2] == ["automations", "add"]]
    assert len(adds) == 1
    add = adds[0]
    assert "30 21 * * *" in add
    assert "--webhook" in add and "127.0.0.1:8899/api/core-run" in " ".join(add)
