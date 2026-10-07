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


def _meter_sample(reading: float, at: float) -> EntitySample:
    return EntitySample(
        entity_id="sensor.home_energy",
        state=str(reading),
        attrs={"unit_of_measurement": "kWh"},
        last_changed_ts=at,
    )


def test_energy_spike_via_flags_for_day_tracking():
    """A meter pushed over several days flags a jump vs the 14-day median."""
    checker = Checker()
    day0 = time.mktime((2026, 9, 1, 8, 0, 0, 0, 0, -1))
    reading = 1000.0
    # Baseline: 3 quiet days (+1.0 kWh each).
    for i in range(3):
        day = day0 + i * 86400
        checker.flags_for([_meter_sample(reading, day)], day)
        reading += 1.0
        checker.flags_for([_meter_sample(reading, day + 3600)], day + 3600)
    # Day 4: consumption jumps 10x during the day.
    spike_day = day0 + 3 * 86400
    checker.flags_for([_meter_sample(reading, spike_day)], spike_day)
    reading += 10.0
    checker.flags_for([_meter_sample(reading, spike_day + 3600)], spike_day + 3600)
    # The spike is only measurable on the next day's first sample.
    flags = checker.flags_for(
        [_meter_sample(reading, spike_day + 86400)], spike_day + 86400
    )
    assert "energy_spike:sensor.home_energy" in flags


def test_energy_meter_reset_restarts_tracking():
    checker = Checker()
    day0 = time.mktime((2026, 9, 1, 8, 0, 0, 0, 0, -1))
    checker.flags_for([_meter_sample(5000.0, day0)], day0)
    # The meter got replaced: reading drops — no flag, fresh baseline.
    flags = checker.flags_for([_meter_sample(3.0, day0 + 86400)], day0 + 86400)
    assert "energy_spike:sensor.home_energy" not in flags
    assert checker._meters["sensor.home_energy"]["day_start"] == 3.0


def test_energy_non_kwh_units_ignored():
    checker = Checker()
    sample = EntitySample(
        entity_id="sensor.power",
        state="1500",
        attrs={"unit_of_measurement": "W"},
        last_changed_ts=NOW,
    )
    assert checker.flags_for([sample], NOW) == []
