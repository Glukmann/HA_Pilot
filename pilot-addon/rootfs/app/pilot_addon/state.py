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

from .chatsessions import ChatSessions
from .coregate import pending_update
from .eventlog import EventLog
from .modelstore import resolve_section

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

# Config schema versioning (docs/2026-10-07-openclaw-update-policy.md):
# v1 — original flat file (no schema_version field);
# v2 — model profiles in supervisor.models/active_id (0.10.0); v1 files stay
#      valid via the legacy fallback, we just stamp the version.
# v3 — OpenClaw core bundled in the image (0.18.0); the openclaw section
#      records that the core is managed by the add-on.
CONFIG_SCHEMA_VERSION = 3


def migrate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Bring a parsed config dict up to the current schema, in memory.

    Each step is small and deterministic; a file newer than this code is
    returned untouched (forward compatibility).
    """
    version = raw.get("schema_version")
    if isinstance(version, int) and version > CONFIG_SCHEMA_VERSION:
        return raw
    version = version if isinstance(version, int) else 1
    if version < 2:
        # v1 -> v2: model profiles introduced. Legacy top-level supervisor
        # fields remain valid via modelstore's fallback — nothing to move,
        # only the version stamp changes.
        version = 2
    if version < 3:
        # v2 -> v3: the OpenClaw core ships in the image; the section only
        # records that it is managed by the add-on. An existing section
        # (e.g. disabled by hand) is preserved — overlay wins below.
        existing = raw.get("openclaw")
        raw["openclaw"] = {
            "enabled": True,
            **(existing if isinstance(existing, dict) else {}),
        }
        version = 3
    raw["schema_version"] = CONFIG_SCHEMA_VERSION
    return raw


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
            unit = sample.get("attrs", {}).get("unit_of_measurement")
            rendered = f"{state} {unit}" if unit else str(state)
            area = sample.get("area") or "No room"
            groups.setdefault(area, []).append(f"  {name}: {rendered}")
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
        self.runtime_version = "0.20.5"
        self.started_ts = time.time()
        self.persona: dict[str, int] = {slider: 50 for slider in PERSONA_SLIDERS}
        self.persona_preset = "butler"
        self.mode = "normal"
        self.current_focus = ""
        self.daily_budget = 10.0
        self.cost_today = 0.0
        self.cost_day = time.strftime("%Y-%m-%d", time.localtime())
        self._load_cost(Path(data_dir) / "cost.json")
        self.supervisor_status: dict[str, Any] = {
            "last_run_ts": None,
            "last_decisions": 0,
        }
        self.vitrine = VitrineState()
        self.flags: list[str] = []
        self.sessions = ChatSessions(Path(data_dir))
        self.events = EventLog(Path(data_dir) / "events.jsonl")
        self.supervisor_busy = False  # ручной run из мастерской (ws)
        self.core_alive = False  # bundled OpenClaw gateway (merge stage 1)
        self.core_last_seen_ts: float | None = None
        self.heartbeat_estimated_day = ""  # daily cost estimate marker

    def push_vitrine(self, states: dict[str, dict[str, Any]]) -> None:
        """Merge a pushed batch into the vitrine and journal transitions."""
        previous = {
            eid: sample.get("state")
            for eid, sample in self.vitrine.states.items()
            if isinstance(sample, dict)
        }
        self.vitrine.update(states)
        for eid, sample in states.items():
            if not isinstance(sample, dict):
                continue
            new_state = sample.get("state")
            if previous.get(eid) != new_state:
                self.events.record(eid, str(new_state))

    # -- cost accounting (persisted: the budget guard must survive restarts) --
    def _load_cost(self, path: Path) -> None:
        self._cost_path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        day = raw.get("day")
        if day == self.cost_day:
            try:
                self.cost_today = float(raw.get("total") or 0.0)
            except (TypeError, ValueError):
                pass

    def _save_cost(self) -> None:
        try:
            tmp = self._cost_path.with_name(self._cost_path.name + ".tmp")
            tmp.write_text(
                json.dumps({"day": self.cost_day, "total": self.cost_today}),
                encoding="utf-8",
            )
            tmp.replace(self._cost_path)
        except OSError:
            pass  # cost persistence is best-effort; the guard tolerates it

    def accrue_cost(self, amount: float) -> None:
        """Add LLM spend to today's counter and persist it."""
        self.cost_today += amount
        self._save_cost()

    def reset_cost(self) -> None:
        """Zero the daily counter (Reset all) and persist."""
        self.cost_today = 0.0
        self._save_cost()

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
        self._save_cost()
        return True

    # -- persona / policy -------------------------------------------------
    @property
    def config_path(self) -> Path:
        """Runtime configuration file behind the WS config commands."""
        return Path(self.data_dir) / "pilot.json"

    def read_config(self) -> dict[str, Any] | None:
        """Parsed pilot.json, schema migrations applied; None when unusable."""
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return migrate_config(raw)

    def migrate_config_file(self) -> bool:
        """Apply config migrations to disk; True when the file changed.

        Called once at startup: older schemas are stamped up-front so every
        later write_config_section keeps the migrated shape.
        """
        try:
            original = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(original, dict):
            return False
        migrated = migrate_config(dict(original))
        if migrated == original:
            return False
        tmp = self.config_path.with_name("pilot.json.tmp")
        tmp.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.config_path)
        return True

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
        merged = migrate_config(dict(current or {}))
        existing = merged.get(section)
        base = dict(existing) if isinstance(existing, dict) else {}
        merged[section] = _deep_merge(base, values)
        tmp = self.config_path.with_name("pilot.json.tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(self.config_path)

    def is_onboarded(self) -> bool:
        """True when the resolved supervisor config has base_url+api_key+model.

        The wizard finishes by writing that section, so the flag rises on
        its own; with model profiles the active one counts (modelstore).
        A missing or broken config means "not onboarded".
        """
        raw = self.read_config() or {}
        supervisor = raw.get("supervisor")
        if not isinstance(supervisor, dict):
            return False
        resolved = resolve_section(supervisor)
        return all(resolved.get(key) for key in ("base_url", "api_key", "model"))

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
            "core": {
                "alive": self.core_alive,
                "last_run_ts": self.core_last_seen_ts,
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
            "learning": self.queue.as_learning() if self.queue is not None else {},
            "core_update": pending_update(Path(self.data_dir)),
            "supervisor": {
                **self.supervisor_status,
                "cost_today": round(self.cost_today, 4),
            },
        }
