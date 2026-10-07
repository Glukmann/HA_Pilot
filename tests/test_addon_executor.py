"""Tests for the trust-queue action executor — no network: fake HA backend."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json

from aiohttp import web
from pilot_addon import executor
from pilot_addon.executor import ExecutorError, HaExecutor
from pilot_addon.main import build_state
import pytest


class FakeHa:
    """Records HA service calls; serves configurable status codes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self.status = 200

    @property
    def service_calls(self) -> list[tuple[str, dict]]:
        return [(path, body) for path, body, _h in self.calls]


@pytest.fixture
async def fake_ha(socket_enabled, monkeypatch) -> AsyncIterator[FakeHa]:
    backend = FakeHa()

    async def _service(request: web.Request) -> web.Response:
        body = await request.json()
        backend.calls.append((request.path, body, dict(request.headers)))
        return web.json_response({"ok": True}, status=backend.status)

    app = web.Application()
    app.router.add_post("/api/services/{domain}/{service}", _service)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(executor, "HA_API_BASE", f"http://127.0.0.1:{port}/api")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "exec-test-token")
    yield backend
    await runner.cleanup()


async def test_setpoint_applies_via_ha_api(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {
            "type": "supervisor",
            "kind": "setpoint",
            "entity_id": "number.comfort",
            "value": 22,
        }
    )
    assert result.status == "applied"
    assert result.detail["entity_id"] == "number.comfort"
    assert fake_ha.service_calls == [
        (
            "/api/services/number/set_value",
            {"entity_id": "number.comfort", "value": 22.0},
        )
    ]
    headers = fake_ha.calls[0][2]
    assert headers.get("Authorization") == "Bearer exec-test-token"


async def test_input_number_domain_also_executable(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {"kind": "setpoint", "entity_id": "input_number.guest_temp", "value": 21}
    )
    assert result.status == "applied"
    assert fake_ha.service_calls[0][0] == "/api/services/input_number/set_value"


async def test_proposal_is_acknowledged_without_ha_call(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {"type": "supervisor", "kind": "proposal", "title": "Почистить фильтр"}
    )
    assert result.status == "ack"
    assert result.detail["title"] == "Почистить фильтр"
    assert fake_ha.calls == []


async def test_refuses_non_setpoint_domain(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {
            "type": "supervisor",
            "kind": "setpoint",
            "entity_id": "switch.gate",
            "value": 1,
        }
    )
    assert result.status == "refused"
    assert result.detail["reason"] == "domain_not_executable"
    assert fake_ha.calls == []


async def test_refuses_unknown_and_malformed_actions(fake_ha: FakeHa):
    assert (
        await HaExecutor().apply({"type": "supervisor", "kind": "proposal-x"})
    ).status == "refused"
    assert (
        await HaExecutor().apply({"kind": "setpoint", "entity_id": "number.x"})
    ).status == "refused"
    assert (
        await HaExecutor().apply(
            {"kind": "setpoint", "entity_id": "number.x", "value": True}
        )
    ).status == "refused"
    assert fake_ha.calls == []


async def test_rollback_resolves_entity_from_key(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {"type": "rollback", "rollback_key": "setpoint:number.comfort", "value": 20}
    )
    assert result.status == "applied"
    assert fake_ha.service_calls == [
        (
            "/api/services/number/set_value",
            {"entity_id": "number.comfort", "value": 20.0},
        )
    ]


async def test_rollback_with_foreign_key_refused(fake_ha: FakeHa):
    result = await HaExecutor().apply(
        {"type": "rollback", "rollback_key": "gate", "value": "off"}
    )
    assert result.status == "refused"
    assert fake_ha.calls == []


async def test_ha_http_error_raises(fake_ha: FakeHa):
    fake_ha.status = 500
    with pytest.raises(ExecutorError, match="HA HTTP 500"):
        await HaExecutor().apply(
            {"kind": "setpoint", "entity_id": "number.comfort", "value": 22}
        )


async def _audit_events(tmp_path) -> list[str]:
    try:
        text = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    except OSError:
        return []
    return [json.loads(line)["event"] for line in text.strip().splitlines()]


async def test_confirm_yes_executes_in_background(fake_ha: FakeHa, tmp_path):
    state = build_state(tmp_path)
    item_id = state.queue.propose(
        "Уставка 22",
        {
            "type": "supervisor",
            "kind": "setpoint",
            "entity_id": "number.comfort",
            "value": 22,
            "previous": 20,
            "rollback_key": "setpoint:number.comfort",
        },
    )
    assert state.queue.confirm(item_id, "yes") is True
    await asyncio.sleep(0.2)  # let the executor task run
    assert fake_ha.service_calls == [
        (
            "/api/services/number/set_value",
            {"entity_id": "number.comfort", "value": 22.0},
        )
    ]
    assert "action.executor.applied" in await _audit_events(tmp_path)
    assert state.queue._rollback.previous("setpoint:number.comfort") == 20


async def test_ha_failure_requeues_for_owner_retry(fake_ha: FakeHa, tmp_path):
    state = build_state(tmp_path)
    action = {
        "type": "supervisor",
        "kind": "setpoint",
        "entity_id": "number.comfort",
        "value": 22,
        "rollback_key": "setpoint:number.comfort",
    }
    item_id = state.queue.propose("Уставка 22", dict(action))
    assert state.queue.confirm(item_id, "yes") is True

    fake_ha.status = 503
    await asyncio.sleep(0.2)
    events = await _audit_events(tmp_path)
    assert "action.executor.error" in events
    # The failed action is back in the queue, marked as a manual retry.
    assert len(state.queue.items) == 1
    retry_item = state.queue.items[0]
    assert retry_item.action["retry"] is True
    assert retry_item.action["entity_id"] == "number.comfort"

    # Owner confirms the retry — this time HA is healthy.
    fake_ha.status = 200
    assert state.queue.confirm(retry_item.id, "yes") is True
    await asyncio.sleep(0.2)
    # The failed 503 attempt and the successful retry were both sent to HA.
    assert len(fake_ha.service_calls) == 2
    assert fake_ha.service_calls[-1] == (
        "/api/services/number/set_value",
        {"entity_id": "number.comfort", "value": 22.0},
    )
    assert "action.executor.applied" in await _audit_events(tmp_path)
    assert state.queue.items == []


def test_apply_without_event_loop_is_skipped(tmp_path):
    """A sync caller with no loop gets an audit record, not a crash."""
    state = build_state(tmp_path)
    state.queue._apply_action({"kind": "setpoint", "entity_id": "number.x", "value": 1})
    text = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in text.strip().splitlines()]
    assert "action.executor.skipped" in events


async def test_deviation_service_call_executes_when_whitelisted(fake_ha: FakeHa):
    """A confirmed 'turn it off' deviation really calls HA — lights allowed."""
    result = await HaExecutor().apply(
        {
            "type": "deviation",
            "domain": "light",
            "service": "turn_off",
            "entity_id": "light.hall",
            "data": {},
        }
    )
    assert result.status == "applied"
    assert fake_ha.service_calls == [
        ("/api/services/light/turn_off", {"entity_id": "light.hall"})
    ]


async def test_deviation_service_call_refused_outside_whitelist(fake_ha: FakeHa):
    """Switches and cameras stay non-executable even as explicit calls."""
    for action in (
        {
            "type": "deviation",
            "domain": "switch",
            "service": "turn_off",
            "entity_id": "switch.pump",
            "data": {},
        },
        {
            "type": "deviation",
            "domain": "camera",
            "service": "turn_off",
            "entity_id": "camera.porch",
            "data": {},
        },
    ):
        result = await HaExecutor().apply(action)
        assert result.status == "refused"
    assert fake_ha.calls == []


async def test_setpoint_actions_still_use_setpoint_path(fake_ha: FakeHa):
    """The generic-call branch must not swallow classic setpoint actions."""
    result = await HaExecutor().apply(
        {"type": "supervisor", "kind": "setpoint", "entity_id": "number.x", "value": 21}
    )
    assert result.status == "applied"
    assert fake_ha.service_calls == [
        ("/api/services/number/set_value", {"entity_id": "number.x", "value": 21.0})
    ]
