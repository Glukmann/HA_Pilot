"""Runtime state for the Pilot add-on.

Single source of truth for everything the integration's HTTP contract
exposes. Persistence: data/pilot.yaml (config + persona policy); the
confirmation queue and audit log are append-only files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
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


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; overlay wins, non-dict values replace."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


@dataclass
class VitrineState:
    """Last known home data snapshot (the 'vitrine').

    States are pushed by the HA integration (the integration IS HA, so no
    token is ever needed); the addon renders the human-readable card.
    """

    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_event_ts: float = 0.0
    connected: bool = False

    @property
    def age_s(self) -> float:
        if not self.last_event_ts:
            return float("inf")
        return max(0.0, time.time() - self.last_event_ts)

    @property
    def fresh(self) -> bool:
        return self.connected and self.age_s <= VITRINE_MAX_AGE_S

    @property
    def lines(self) -> list[str]:
        """Render one human-readable fact per entity, grouped by room (area).

        The agent reads the home much better with room context; entities
        without an area go under "No room", always last.
        """
        groups: dict[str, list[str]] = {}
        for eid, sample in sorted(self.states.items()):
            name = sample.get("attrs", {}).get("friendly_name") or eid
            state = sample.get("state", "?")
            area = sample.get("area") or "No room"
            groups.setdefault(area, []).append(f"  {name}: {state}")
        lines: list[str] = []
        for area in sorted(groups, key=lambda a: (a == "No room", a)):
            lines.append(f"{area}:")
            lines.extend(groups[area])
        return lines

    def update(self, states: dict[str, dict[str, Any]]) -> None:
        """Merge a pushed batch of entity states."""
        now = time.time()
        for eid, sample in states.items():
            self.states[eid] = sample
        self.last_event_ts = now
        self.connected = True

    def as_dict(self) -> dict[str, Any]:
        return {"fresh": self.fresh, "lines": self.lines}


class RuntimeState:
    """Mutable runtime state behind the /api contract."""

    def __init__(self, data_dir: str, token: str = "") -> None:
        self.data_dir = data_dir
        self.token = token
        self.status = "ok"
        self.runtime_version = "0.9.0"
        self.started_ts = time.time()
        self.persona: dict[str, int] = {slider: 50 for slider in PERSONA_SLIDERS}
        self.persona_preset = "butler"
        self.mode = "normal"
        self.current_focus = ""
        self.daily_budget = 10.0
        self.cost_today = 0.0
        self.cost_day = time.strftime("%Y-%m-%d", time.localtime())
        self.supervisor_status: dict[str, Any] = {
            "last_run_ts": None,
            "last_decisions": 0,
        }
        self.vitrine = VitrineState()
        self.flags: list[str] = []

    def reset_cost_if_new_day(self) -> bool:
        """Reset cost_today on the first event of a new local day.

        Called by the daily supervisor run — the only LLM spender, so its
        budget guard always sees today's figure.
        """
        today = time.strftime("%Y-%m-%d", time.localtime())
        if today == self.cost_day:
            return False
        self.cost_day = today
        self.cost_today = 0.0
        return True

    # -- persona / policy -------------------------------------------------
    @property
    def config_path(self) -> Path:
        """Runtime configuration file behind the WS config commands."""
        return Path(self.data_dir) / "pilot.json"

    def read_config(self) -> dict[str, Any] | None:
        """Parsed pilot.json; None when missing, unreadable or not an object."""
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def write_config_section(self, section: str, values: dict[str, Any]) -> None:
        """Deep-merge values into pilot.json[section]; write atomically.

        Other sections are preserved. A broken existing file is kept as
        pilot.json.bad-<timestamp> next to the fresh config. Raises OSError
        when the write itself fails and ValueError on a bad section name.
        """
        if not section:
            raise ValueError("empty section")
        current = self.read_config()
        if current is None and self.config_path.exists():
            # Broken JSON: keep a copy for post-mortem, start from scratch.
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            backup = self.config_path.with_name(f"pilot.json.bad-{stamp}")
            self.config_path.replace(backup)
            current = {}
        merged = dict(current or {})
        existing = merged.get(section)
        base = dict(existing) if isinstance(existing, dict) else {}
        merged[section] = _deep_merge(base, values)
        tmp = self.config_path.with_name("pilot.json.tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(self.config_path)

    def is_onboarded(self) -> bool:
        """True when the supervisor section has base_url + api_key + model.

        The wizard finishes by writing that section, so the flag rises on
        its own; a missing or broken config means "not onboarded".
        """
        raw = self.read_config() or {}
        supervisor = raw.get("supervisor")
        if not isinstance(supervisor, dict):
            return False
        return all(supervisor.get(key) for key in ("base_url", "api_key", "model"))

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
    checker: Any = None  # bound via attach_checker() when the runtime runs one

    def attach_trust(self, trust: Any) -> None:
        """Bind the trust layer (queue/audit)."""
        self.queue = trust

    def attach_checker(self, checker: Any) -> None:
        """Bind the deterministic checker layer."""
        self.checker = checker

    def layers_status(self) -> dict[str, dict[str, Any]]:
        """Health of the runtime layers for the WS status command."""
        return {
            "vitrine": {
                "alive": self.vitrine.connected,
                "last_run_ts": self.vitrine.last_event_ts or None,
            },
            "checker": {
                "alive": self.checker is not None,
                "last_run_ts": getattr(self.checker, "last_run_ts", None),
            },
            "trust": {
                "alive": self.queue is not None,
                "last_run_ts": getattr(self.queue, "last_change_ts", None),
            },
        }

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
            "uptime_s": int(time.time() - self.started_ts),
            "layers": self.layers_status(),
            "onboarded": self.is_onboarded(),
            "supervisor": {
                **self.supervisor_status,
                "cost_today": round(self.cost_today, 4),
            },
        }
