"""Tests for the add-on checker (deterministic supervisor flags)."""

import time

from pilot_addon.checker import Checker, EntitySample

NOW = time.time()


def _sample(
    eid: str, state: str, attrs: dict | None = None, changed: float | None = None
):
    return EntitySample(
        entity_id=eid,
        state=state,
        attrs=attrs or {},
        last_changed_ts=changed if changed is not None else NOW,
    )


def test_sensor_dead_flag():
    checker = Checker()
    dead = _sample("sensor.temp", "unavailable", changed=NOW - 7 * 3600)
    assert checker.check_sensor_dead(dead, NOW) is True
    alive = _sample("sensor.temp", "22.4")
    assert checker.check_sensor_dead(alive, NOW) is False
    recently = _sample("sensor.temp", "unavailable", changed=NOW - 60)
    assert checker.check_sensor_dead(recently, NOW) is False


def test_climate_no_effect_flag():
    checker = Checker()
    struggling = _sample(
        "climate.bedroom",
        "heat",
        {"current_temperature": 17.2, "temperature": 20},
        changed=NOW - 3 * 3600,
    )
    assert checker.check_climate_no_effect(struggling, NOW) is True
    on_target = _sample(
        "climate.bedroom",
        "heat",
        {"current_temperature": 19.8, "temperature": 20},
        changed=NOW - 3 * 3600,
    )
    assert checker.check_climate_no_effect(on_target, NOW) is False
    off = _sample("climate.bedroom", "off", changed=NOW - 3 * 3600)
    assert checker.check_climate_no_effect(off, NOW) is False


def test_light_always_on_flag():
    checker = Checker()
    stuck = _sample("light.hall", "on", changed=NOW - 13 * 3600)
    # Simulate 13h of continuous ON via history.
    checker._state_history["light.hall"] = [(NOW - 13 * 3600, "on")]
    assert checker.check_light_always_on(stuck, NOW) is True
    fresh_checker = Checker()
    fresh = _sample("light.hall", "on", changed=NOW - 60)
    assert fresh_checker.check_light_always_on(fresh, NOW) is False


def test_gate_stuck_flag():
    checker = Checker()
    stuck = _sample("switch.gate", "on", changed=NOW - 11 * 60)
    assert checker.check_gate_stuck(stuck, NOW) is True
    pulse = _sample("switch.gate", "on", changed=NOW - 5)
    assert checker.check_gate_stuck(pulse, NOW) is False
    closed = _sample("switch.gate", "off", changed=NOW - 3600)
    assert checker.check_gate_stuck(closed, NOW) is False


def test_energy_spike_flag():
    checker = Checker()
    eid = "sensor.kettle_energy"
    base = NOW - 14 * 24 * 3600
    checker._energy_history[eid] = [(base + i * 86400, 1.0) for i in range(10)]
    assert checker.check_energy_spike(eid, 5.0, NOW) is True  # 5x median
    assert checker.check_energy_spike(eid, 1.1, NOW) is False


def test_flags_for_collects_all():
    checker = Checker()
    checker._state_history["light.hall"] = [(NOW - 13 * 3600, "on")]
    samples = [
        _sample("sensor.temp", "unavailable", changed=NOW - 7 * 3600),
        _sample("switch.gate", "on", changed=NOW - 11 * 60),
        _sample("light.hall", "on", changed=NOW - 13 * 3600),
    ]
    flags = checker.flags_for(samples, NOW)
    assert "sensor_dead:sensor.temp" in flags
    assert "gate_stuck:switch.gate" in flags
    assert "light_always_on:light.hall" in flags
