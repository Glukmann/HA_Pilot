"""Cycle 1.3: learning stats and the daily cost counter persist across restarts."""

from __future__ import annotations

import json

from pilot_addon.main import build_state
from pilot_addon.state import RuntimeState


def test_learning_stats_survive_restart(tmp_path):
    state = build_state(tmp_path)
    item = state.queue.propose("A?", {"type": "x", "entity_id": "light.hall"})
    state.queue.confirm(item, "yes")
    state.queue.propose("B?", {"type": "y", "entity_id": "switch.pump"})
    assert state.queue.stats["accepted"] == 1

    reborn = build_state(tmp_path)
    assert reborn.queue.stats["accepted"] == 1
    assert reborn.queue.stats["proposed"] == 2
    assert reborn.queue.as_learning()["by_kind"]["light"] == {
        "accepted": 1,
        "rejected": 0,
    }


def test_learning_stats_reset_persists(tmp_path):
    state = build_state(tmp_path)
    item = state.queue.propose("A?", {"type": "x"})
    state.queue.confirm(item, "yes")
    state.queue.reset_stats()
    reborn = build_state(tmp_path)
    assert reborn.queue.stats["accepted"] == 0
    assert reborn.queue.as_learning()["by_kind"] == {}


def test_queue_without_path_keeps_stats_memory_only(tmp_path):
    from pilot_addon.trust import AuditLog, RollbackRegistry, TrustQueue

    queue = TrustQueue(
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        rollback=RollbackRegistry(),
        apply_action=lambda action: None,
    )
    queue.propose("A?", {"type": "x"})
    assert not (tmp_path / "learning.json").exists()


def test_cost_counter_survives_restart(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.accrue_cost(1.25)
    state.accrue_cost(0.1)
    assert state.cost_today == 1.35

    reborn = RuntimeState(str(tmp_path))
    assert reborn.cost_today == 1.35
    # The budget guard sees the persisted figure immediately.
    assert reborn.cost_today < reborn.daily_budget


def test_cost_counter_new_day_resets_and_persists(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.accrue_cost(2.0)
    # Simulate the calendar moving on: yesterday's record must not leak.
    state.cost_day = "2000-01-01"
    assert state.reset_cost_if_new_day() is True
    assert state.cost_today == 0.0
    reborn = RuntimeState(str(tmp_path))
    assert reborn.cost_today == 0.0


def test_cost_reset_persists(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.accrue_cost(5.0)
    state.reset_cost()
    assert RuntimeState(str(tmp_path)).cost_today == 0.0


def test_broken_cost_file_degrades_to_zero(tmp_path):
    (tmp_path / "cost.json").write_text("{broken", encoding="utf-8")
    assert RuntimeState(str(tmp_path)).cost_today == 0.0
    (tmp_path / "cost.json").write_text(
        json.dumps({"day": "2999-01-01", "total": 99}), encoding="utf-8"
    )
    # A record from another day never inflates today's counter.
    assert RuntimeState(str(tmp_path)).cost_today == 0.0
