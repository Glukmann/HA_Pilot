"""Stage-1 merge runtime: the 'core' layer status and config schema v3."""

from __future__ import annotations

from pathlib import Path

from pilot_addon.state import CONFIG_SCHEMA_VERSION, RuntimeState, migrate_config


def test_schema_v3_adds_openclaw_section():
    assert CONFIG_SCHEMA_VERSION == 3
    migrated = migrate_config({"schema_version": 2, "supervisor": {}})
    assert migrated["openclaw"] == {"enabled": True}
    assert migrated["schema_version"] == 3


def test_schema_v3_preserves_existing_openclaw_section():
    raw = {"schema_version": 2, "openclaw": {"enabled": False, "port": 18799}}
    migrated = migrate_config(raw)
    assert migrated["openclaw"]["port"] == 18799


def test_layers_status_includes_core(tmp_path: Path):
    state = RuntimeState(str(tmp_path))
    layers = state.layers_status()
    assert layers["core"] == {"alive": False, "last_run_ts": None}
    state.core_alive = True
    state.core_last_seen_ts = 123.0
    layers = state.layers_status()
    assert layers["core"] == {"alive": True, "last_run_ts": 123.0}


def test_runtime_version_is_0180(tmp_path: Path):
    state = RuntimeState(str(tmp_path))
    assert state.runtime_version == "0.18.0"
