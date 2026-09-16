"""Deterministic supervisor checker (0 tokens): deviation flags.

Reproduces the check table from docs/2026-09-13-supervisor-design.md:
sensor_dead, climate_no_effect, energy_spike, light_always_on, gate_stuck.
Flags feed the trust queue and the daily LLM supervisor run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "sensor_dead_s": 6 * 3600,
    "climate_no_effect_delta": 2.0,
    "climate_no_effect_s": 2 * 3600,
    "energy_spike_factor": 3.0,
    "light_always_on_s": 12 * 3600,
    "gate_stuck_s": 10 * 60,
}

# Impulse-relay hint: gate_stuck only applies to entities that look like
# gates/garage/doors — a plain heater or fan left on must not flag.
GATE_MARKERS = (
    "gate",
    "garage",
    "door",
    "kalitka",
    "vorota",
    "калитк",
    "ворот",
    "двер",
)


@dataclass
class EntitySample:
    """One observed data point for checkers."""

    entity_id: str
    state: str
    attrs: dict[str, Any] = field(default_factory=dict)
    last_changed_ts: float = field(default_factory=time.time)


class Checker:
    """Compute deviation flags from vitrine/entity samples."""

    def __init__(self, thresholds: dict[str, Any] | None = None) -> None:
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._state_history: dict[str, list[tuple[float, str]]] = {}
        self._energy_history: dict[str, list[tuple[float, float]]] = {}
        self.last_run_ts: float | None = None

    def check_sensor_dead(self, sample: EntitySample, now: float | None = None) -> bool:
        """Entity unavailable/unknown longer than the threshold."""
        now = now or time.time()
        if sample.state not in ("unavailable", "unknown"):
            return False
        return bool(now - sample.last_changed_ts > self.thresholds["sensor_dead_s"])

    def check_climate_no_effect(
        self, sample: EntitySample, now: float | None = None
    ) -> bool:
        """HVAC running but the room drifts away from the setpoint."""
        now = now or time.time()
        if not sample.entity_id.startswith("climate."):
            return False
        if sample.state in ("off", "unavailable", "unknown"):
            return False
        current = sample.attrs.get("current_temperature")
        target = sample.attrs.get("temperature")
        if current is None or target is None:
            return False
        if (
            abs(float(current) - float(target))
            < self.thresholds["climate_no_effect_delta"]
        ):
            return False
        return bool(
            now - sample.last_changed_ts > self.thresholds["climate_no_effect_s"]
        )

    def check_light_always_on(
        self, sample: EntitySample, now: float | None = None
    ) -> bool:
        """Light ON continuously longer than the threshold."""
        now = now or time.time()
        if not sample.entity_id.startswith("light."):
            return False
        history = self._state_history.setdefault(sample.entity_id, [])
        history.append((now, sample.state))
        history[:] = [(ts, st) for ts, st in history if now - ts <= 24 * 3600]
        if sample.state != "on":
            return False
        return bool(
            all(st == "on" for _, st in history)
            and now - history[0][0] > self.thresholds["light_always_on_s"]
        )

    def check_gate_stuck(self, sample: EntitySample, now: float | None = None) -> bool:
        """Impulse relay (gate/garage/door) ON longer than the threshold.

        Only applies to entities that look like impulse relays (entity id or
        friendly name) — in a real home plenty of devices stay on for hours
        by design, and flagging them would be pure noise.
        """
        now = now or time.time()
        if sample.state != "on":
            return False
        if not self._looks_like_gate(sample):
            return False
        return bool(now - sample.last_changed_ts > self.thresholds["gate_stuck_s"])

    @staticmethod
    def _looks_like_gate(sample: EntitySample) -> bool:
        friendly_name = str(sample.attrs.get("friendly_name", ""))
        haystack = f"{sample.entity_id} {friendly_name}".lower()
        return any(marker in haystack for marker in GATE_MARKERS)

    def check_energy_spike(
        self, entity_id: str, daily_kwh: float, now: float | None = None
    ) -> bool:
        """Daily energy jump above N times the 14-day median."""
        now = now or time.time()
        history = self._energy_history.setdefault(entity_id, [])
        history.append((now, daily_kwh))
        history[:] = [(ts, v) for ts, v in history if now - ts <= 14 * 24 * 3600]
        if len(history) < 3:
            return False
        values = sorted(v for _, v in history[:-1])
        median = values[len(values) // 2]
        return bool(daily_kwh > median * self.thresholds["energy_spike_factor"])

    def flags_for(
        self, samples: list[EntitySample], now: float | None = None
    ) -> list[str]:
        """Run all checks over samples; return flag strings."""
        now = now or time.time()
        self.last_run_ts = now
        flags: list[str] = []
        for sample in samples:
            eid = sample.entity_id
            if self.check_sensor_dead(sample, now):
                flags.append(f"sensor_dead:{eid}")
            if self.check_climate_no_effect(sample, now):
                flags.append(f"climate_no_effect:{eid}")
            if self.check_light_always_on(sample, now):
                flags.append(f"light_always_on:{eid}")
            if self.check_gate_stuck(sample, now):
                flags.append(f"gate_stuck:{eid}")
        return flags
