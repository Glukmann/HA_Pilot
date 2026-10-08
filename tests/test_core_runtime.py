"""Stage-1 merge runtime: the 'core' layer status and config schema v3."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time

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


def test_runtime_version_is_0194(tmp_path: Path):
    state = RuntimeState(str(tmp_path))
    assert state.runtime_version == "0.19.4"


async def _fake_health(_session, **_kwargs):
    return {"alive": True, "detail": "ok"}


async def test_core_health_loop_updates_state(tmp_path, monkeypatch):
    from pilot_addon import main as main_module

    monkeypatch.setattr(main_module.corehttp, "core_health", _fake_health)
    state = RuntimeState(str(tmp_path))
    task = asyncio.create_task(main_module.core_health_loop(state, interval_s=3600))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if state.core_alive:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.core_alive is True
    assert state.core_last_seen_ts and state.core_last_seen_ts <= time.time()


async def test_core_health_loop_swallows_probe_errors(tmp_path, monkeypatch):
    from pilot_addon import main as main_module

    async def _boom(_session, **_kwargs):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(main_module.corehttp, "core_health", _boom)
    state = RuntimeState(str(tmp_path))
    task = asyncio.create_task(main_module.core_health_loop(state, interval_s=3600))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.core_alive is False
