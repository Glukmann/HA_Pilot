"""Runtime state for the Pilot add-on.

Single source of truth for everything the integration's HTTP contract
exposes. Persistence: data/pilot.yaml (config + persona policy); the
confirmation queue and audit log are append-only files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

PERSONA_SLIDERS = (
    "butler_observer",
    "politeness",
    "verbosity",
    "conservative",
)
PERSONA_PRESETS: dict[str, dict[str, int]] = {
    "butler": {
        "butler_observer": 80,
        "politeness": 30,
        "verbosity": 70,
        "conservative": 40,
    },
    "observer": {
        "butler_observer": 10,
        "politeness": 20,
        "verbosity": 20,
        "conservative": 70,
    },
    "economy": {
        "butler_observer": 40,
        "politeness": 60,
        "verbosity": 30,
        "conservative": 80,
    },
}
PILOT_MODES = ("normal", "vacation", "guests", "sick")
VITRINE_MAX_AGE_S = 60


@dataclass
class VitrineState:
    """Last known home data snapshot (the 'vitrine')."""

    lines: list[str] = field(default_factory=list)
    last_event_ts: float = 0.0
    ws_ok: bool = False

    @property
    def age_s(self) -> float:
        if not self.last_event_ts:
            return float("inf")
        return max(0.0, time.time() - self.last_event_ts)

    @property
    def fresh(self) -> bool:
        return self.ws_ok and self.age_s <= VITRINE_MAX_AGE_S

    def as_dict(self) -> dict[str, Any]:
        return {"fresh": self.fresh, "lines": list(self.lines)}


class RuntimeState:
    """Mutable runtime state behind the /api contract."""

    def __init__(self, data_dir: str, token: str = "") -> None:
        self.data_dir = data_dir
        self.token = token
        self.status = "ok"
        self.runtime_version = "0.4.0"
        self.persona: dict[str, int] = {slider: 50 for slider in PERSONA_SLIDERS}
        self.persona_preset = "butler"
        self.mode = "normal"
        self.current_focus = ""
        self.daily_budget = 10.0
        self.cost_today = 0.0
        self.vitrine = VitrineState()
        self.flags: list[str] = []

    # -- persona / policy -------------------------------------------------
    def set_persona(self, slider: str, value: int) -> None:
        if slider not in PERSONA_SLIDERS:
            raise ValueError(f"unknown slider: {slider}")
        self.persona[slider] = max(0, min(100, int(value)))

    def apply_preset(self, preset: str) -> None:
        if preset not in PERSONA_PRESETS:
            raise ValueError(f"unknown preset: {preset}")
        self.persona = dict(PERSONA_PRESETS[preset])
        self.persona_preset = preset

    def set_mode(self, mode: str) -> None:
        if mode not in PILOT_MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.mode = mode

    # -- status snapshot ---------------------------------------------------
    @property
    def queue_size(self) -> int:
        return len(self.queue.items)

    @property
    def awaiting_confirmation(self) -> bool:
        return self.queue_size > 0

    queue: Any = None  # bound in __post_init__ via attach_trust()

    def attach_trust(self, trust: Any) -> None:
        """Bind the trust layer (queue/audit)."""
        self.queue = trust

    def snapshot(self) -> dict[str, Any]:
        """Full /api/status payload consumed by the HA integration."""
        age = self.vitrine.age_s
        return {
            "status": self.status,
            "vitrine_age_s": None if age == float("inf") else int(age),
            "cost_today": round(self.cost_today, 4),
            "queue_size": self.queue_size,
            "awaiting_confirmation": self.awaiting_confirmation,
            "runtime_version": self.runtime_version,
            "persona": dict(self.persona),
            "daily_budget": self.daily_budget,
            "persona_preset": self.persona_preset,
            "mode": self.mode,
            "current_focus": self.current_focus,
            "flags": list(self.flags),
        }
