"""Tests for the LLM supervisor run — no network: fake LLM + HA backend."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import json
import time

from aiohttp import web
from pilot_addon import supervisor
from pilot_addon.checker import Checker
from pilot_addon.main import build_state, run_checker_pass
from pilot_addon.state import RuntimeState
from pilot_addon.supervisor import run_supervisor
import pytest

DEAD_SENSOR_AGE_S = 7 * 3600  # beyond the 6 h sensor_dead threshold


class FakeBackend:
    """Records requests; serves canned LLM answers and HA service calls."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict, dict]] = []
        self.llm_content: str = '{"summary": "ok", "decisions": []}'
        self.ha_status = 200

    @property
    def llm_calls(self) -> int:
        chat = [r for r in self.requests if r[0].endswith("/chat/completions")]
        return len(chat)

    @property
    def ha_calls(self) -> list[tuple[str, dict]]:
        return [(p, b) for p, b, _h in self.requests if p.startswith("/api/services/")]


Env = tuple[RuntimeState, Checker, FakeBackend, Callable[[dict | None], None], dict]


@pytest.fixture
async def env(tmp_path, socket_enabled, monkeypatch) -> AsyncIterator[Env]:
    """State + fresh checker + fake LLM/HA server on an ephemeral port."""
    state = build_state(tmp_path)
    checker = Checker()
    state.attach_checker(checker)
    backend = FakeBackend()

    async def _llm(request: web.Request) -> web.Response:
        body = await request.json()
        backend.requests.append(("/v1/chat/completions", body, dict(request.headers)))
        return web.json_response(
            {
                "choices": [{"message": {"content": backend.llm_content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        )

    async def _ha_service(request: web.Request) -> web.Response:
        body = await request.json()
        backend.requests.append((request.path, body, dict(request.headers)))
        return web.json_response({"ok": True}, status=backend.ha_status)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", _llm)
    app.router.add_post("/api/services/{domain}/{service}", _ha_service)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    monkeypatch.setattr(supervisor, "HA_API_BASE", f"http://127.0.0.1:{port}/api")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

    base_cfg = {
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key": "k",
        "model": "test-model",
        "price_input_per_1m": 10.0,
        "price_output_per_1m": 20.0,
    }

    def _write_config(cfg: dict | None) -> None:
        state.config_path.write_text(
            json.dumps({"supervisor": cfg} if cfg else {}), encoding="utf-8"
        )

    yield state, checker, backend, _write_config, base_cfg
    await runner.cleanup()


def _sample(state: str, attrs: dict | None = None, last_changed: float | None = None):
    return {
        "state": state,
        "attrs": attrs or {},
        "last_changed": last_changed if last_changed is not None else time.time(),
        "area": "Test",
    }


def _dead_sensor() -> dict:
    return _sample("unknown", last_changed=time.time() - DEAD_SENSOR_AGE_S)


async def _fresh_flags(state: RuntimeState, checker: Checker, vitrine: dict) -> None:
    """Push vitrine states and run one checker pass (flags + fresh timestamp)."""
    state.vitrine.update(vitrine)
    await run_checker_pass(state, checker)


def _audit_text(tmp_path) -> str:
    try:
        return (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    except OSError:
        return ""


def _journal_text(tmp_path) -> str:
    try:
        return (tmp_path / "supervisor-log.md").read_text(encoding="utf-8")
    except OSError:
        return ""


async def test_no_config_skips_without_llm(env, tmp_path) -> None:
    """Without the supervisor config the run is skipped and the LLM never called."""
    state, checker, backend, write_config, _cfg = env
    write_config(None)
    await _fresh_flags(state, checker, {"sensor.x": _dead_sensor()})
    result = await run_supervisor(state)
    assert result == {"skipped": "no_config"}
    assert backend.llm_calls == 0
    assert '"reason": "no_config"' in _audit_text(tmp_path)


async def test_budget_guard_skips_when_limit_hit(env, tmp_path) -> None:
    """An exhausted daily budget refuses the run before the LLM call."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    state.cost_today = state.daily_budget
    await _fresh_flags(state, checker, {"sensor.x": _sample("22.4")})
    result = await run_supervisor(state)
    assert result == {"skipped": "budget"}
    assert backend.llm_calls == 0
    assert '"reason": "budget"' in _audit_text(tmp_path)


async def test_quiet_day_journals_without_llm(env, tmp_path) -> None:
    """No flags -> 'all quiet' journal entry, zero tokens spent."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(state, checker, {"sensor.x": _sample("22.4")})
    result = await run_supervisor(state)
    assert result["ok"] is True
    assert result["flags"] == 0
    assert backend.llm_calls == 0
    assert "всё в норме" in _journal_text(tmp_path)
    snap = state.snapshot()
    assert snap["supervisor"]["last_run_ts"] is not None
    assert snap["supervisor"]["cost_today"] == 0.0


async def test_setpoint_within_whitelist_applies_via_ha(env, tmp_path) -> None:
    """A ±3 setpoint decision calls the HA API, journals and accrues cost."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(
        state,
        checker,
        {
            "sensor.temp": _dead_sensor(),
            "input_number.spalnia_target": _sample("20"),
        },
    )
    backend.llm_content = json.dumps(
        {
            "summary": "Спальня остыла ночью",
            "decisions": [
                {
                    "kind": "setpoint",
                    "entity_id": "input_number.spalnia_target",
                    "value": 22,
                    "reason": "догрев спальни",
                }
            ],
        }
    )
    result = await run_supervisor(state)
    assert result["ok"] is True
    assert result["applied"] == 1
    assert result["proposed"] == 0
    assert backend.ha_calls == [
        (
            "/api/services/input_number/set_value",
            {"entity_id": "input_number.spalnia_target", "value": 22},
        )
    ]
    assert state.queue_size == 0
    # (100 in * ₽10 + 50 out * ₽20) / 1M = ₽0.002
    assert state.cost_today == pytest.approx(0.002)
    assert "Изменено: input_number.spalnia_target 20→22" in _journal_text(tmp_path)
    assert '"supervisor.setpoint"' in _audit_text(tmp_path)
    assert state.snapshot()["supervisor"]["last_decisions"] == 1


async def test_setpoint_beyond_whitelist_downgrades_to_proposal(env, tmp_path) -> None:
    """A setpoint outside ±3 is queued for the owner; HA is never called."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(
        state,
        checker,
        {
            "sensor.temp": _dead_sensor(),
            "input_number.spalnia_target": _sample("20"),
        },
    )
    backend.llm_content = json.dumps(
        {
            "summary": "Хочу сильнее греть",
            "decisions": [
                {
                    "kind": "setpoint",
                    "entity_id": "input_number.spalnia_target",
                    "value": 26,
                    "reason": "эксперимент",
                }
            ],
        }
    )
    result = await run_supervisor(state)
    assert result["ok"] is True
    assert result["applied"] == 0
    assert result["proposed"] == 1
    assert backend.ha_calls == []
    assert state.queue_size == 1
    item = state.queue.items[0]
    assert "input_number.spalnia_target" in item.title
    assert item.action["type"] == "supervisor"
    assert "вайтлиста" in item.summary
    assert "На подтверждении" in _journal_text(tmp_path)


async def test_invalid_llm_json_executes_nothing(env, tmp_path) -> None:
    """An unparseable LLM answer is journaled; no HA call, no proposals."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(
        state,
        checker,
        {"sensor.temp": _dead_sensor()},
    )
    backend.llm_content = "это не JSON, честное слово"
    result = await run_supervisor(state)
    assert "parse_error" in result["error"]
    assert backend.llm_calls == 1
    assert backend.ha_calls == []
    assert state.queue_size == 0
    assert '"supervisor.parse_error"' in _audit_text(tmp_path)
    assert "невалидный JSON" in _journal_text(tmp_path)


async def test_proposal_decision_lands_in_trust_queue(env, tmp_path) -> None:
    """kind=proposal goes to the confirmation queue, not to HA."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(
        state,
        checker,
        {"sensor.temp": _dead_sensor()},
    )
    backend.llm_content = json.dumps(
        {
            "summary": "Сенсор мёртв уже неделю",
            "decisions": [
                {
                    "kind": "proposal",
                    "title": "Заменить батарейку датчика температуры",
                    "summary": "Датчик в спальне недоступен больше 6 часов",
                    "reason": "железо чинит человек",
                }
            ],
        }
    )
    result = await run_supervisor(state)
    assert result["applied"] == 0
    assert result["proposed"] == 1
    assert backend.ha_calls == []
    assert state.queue_size == 1
    item = state.queue.items[0]
    assert item.title == "Заменить батарейку датчика температуры"
    assert item.action["type"] == "supervisor"


async def test_stale_checker_skips_run(env, tmp_path) -> None:
    """Flags older than 12 h (or a silent checker) mean the run is skipped."""
    state, checker, backend, write_config, cfg = env
    write_config(cfg)
    # Checker never ran -> no_checker.
    state.flags = ["sensor_dead:x"]
    result = await run_supervisor(state)
    assert result == {"skipped": "no_checker"}
    # Checker ran long ago -> stale_flags.
    checker.last_run_ts = time.time() - 13 * 3600
    result = await run_supervisor(state)
    assert result == {"skipped": "stale_flags"}
    assert backend.llm_calls == 0
    assert "протухли" in _journal_text(tmp_path)


async def test_new_day_resets_cost(env) -> None:
    """The first event of a new local day zeroes cost_today."""
    state, _checker, _backend, write_config, _cfg = env
    write_config(None)
    state.cost_day = "2000-01-01"
    state.cost_today = 5.0
    result = await run_supervisor(state)
    assert result == {"skipped": "no_config"}  # reset happens before the skip
    assert state.cost_today == 0.0
    assert state.cost_day != "2000-01-01"


async def test_run_broadcasts_status(env) -> None:
    """A completed run pushes a fresh status snapshot via the broadcast hook."""
    state, checker, _backend, write_config, cfg = env
    write_config(cfg)
    await _fresh_flags(state, checker, {"sensor.x": _sample("22.4")})
    broadcasted: list[tuple[str, dict]] = []

    async def _broadcast(msg_type: str, payload: dict) -> None:
        broadcasted.append((msg_type, payload))

    await run_supervisor(state, broadcast=_broadcast)
    assert [t for t, _p in broadcasted] == ["status"]
    assert broadcasted[0][1]["supervisor"]["last_run_ts"] is not None
