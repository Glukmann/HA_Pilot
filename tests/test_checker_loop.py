"""Tests for the live checker pipeline: vitrine -> flags -> supervisor."""

from __future__ import annotations

import json
import time

from pilot_addon.checker import Checker
from pilot_addon.main import build_state, run_checker_pass
from pilot_addon.state import RuntimeState

DEAD_SENSOR_AGE_S = 7 * 3600  # beyond the 6 h sensor_dead threshold
GATE_STUCK_AGE_S = 20 * 60  # beyond the 10 min gate_stuck threshold


def _sample(
    state: str,
    attrs: dict | None = None,
    last_changed: float | None = None,
) -> dict:
    """Vitrine sample as pushed by the integration."""
    return {
        "state": state,
        "attrs": attrs or {},
        "last_changed": last_changed if last_changed is not None else time.time(),
        "area": "Test room",
    }


def _setup(tmp_path, vitrine_states: dict) -> tuple[RuntimeState, Checker]:
    state = build_state(tmp_path)
    checker = Checker()
    state.attach_checker(checker)
    state.vitrine.update(vitrine_states)
    return state, checker


async def test_dead_sensor_flag_reaches_state_and_snapshot(tmp_path):
    """A stale unknown sensor becomes a flag after one checker pass."""
    state, checker = _setup(
        tmp_path,
        {
            "sensor.temp": _sample(
                "unknown", last_changed=time.time() - DEAD_SENSOR_AGE_S
            )
        },
    )
    flags = await run_checker_pass(state, checker)

    assert flags == ["sensor_dead:sensor.temp"]
    assert state.flags == ["sensor_dead:sensor.temp"]
    assert state.snapshot()["flags"] == ["sensor_dead:sensor.temp"]
    # The pass is auditable for the supervisor's journal.
    audit = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in audit.strip().splitlines()]
    assert "checker.run" in events


async def test_flag_clears_when_sensor_recovers(tmp_path):
    """Flags are replaced each pass — recovery removes the flag."""
    stale = _sample("unavailable", last_changed=time.time() - DEAD_SENSOR_AGE_S)
    state, checker = _setup(tmp_path, {"sensor.temp": stale})
    assert await run_checker_pass(state, checker) == ["sensor_dead:sensor.temp"]

    state.vitrine.update({"sensor.temp": _sample("22.4")})
    assert await run_checker_pass(state, checker) == []
    assert state.snapshot()["flags"] == []


async def test_gate_stuck_only_flags_gate_like_entities(tmp_path):
    """Plain devices left on are noise; gate-like relays flag."""
    old = time.time() - GATE_STUCK_AGE_S
    state, checker = _setup(
        tmp_path,
        {
            "switch.heater": _sample("on", last_changed=old),
            "switch.kalitka_gate": _sample("on", last_changed=old),
            "switch.relay1": _sample("on", {"friendly_name": "Калитка"}, old),
        },
    )
    flags = await run_checker_pass(state, checker)

    assert "gate_stuck:switch.kalitka_gate" in flags
    assert "gate_stuck:switch.relay1" in flags  # Cyrillic friendly name matches
    assert "gate_stuck:switch.heater" not in flags


async def test_flags_alone_do_not_enqueue_proposals(tmp_path):
    """Flags are advisory until the daily supervisor run proposes actions.

    The old raw flag->queue stub is gone: the checker pass only updates
    state.flags (visible in status), the queue stays empty until an LLM
    supervisor run decides otherwise.
    """
    old = time.time() - DEAD_SENSOR_AGE_S
    state, checker = _setup(
        tmp_path,
        {
            "sensor.temp": _sample("unknown", last_changed=old),
            "sensor.hum": _sample("unavailable", last_changed=old),
        },
    )
    await run_checker_pass(state, checker)
    assert len(state.flags) == 2
    assert state.queue_size == 0
    assert state.snapshot()["flags"] == state.flags
