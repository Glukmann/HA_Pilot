"""Tests for the add-on trust layer."""

from pathlib import Path

from pilot_addon.trust import AuditLog, QueueItem, RollbackRegistry, TrustQueue


def _make_trust(tmp_path: Path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    rollback = RollbackRegistry()
    applied: list[dict] = []
    queue = TrustQueue(
        audit=audit,
        rollback=rollback,
        apply_action=applied.append,
    )
    return queue, audit, rollback, applied


def test_propose_queues_non_whitelisted(tmp_path):
    queue, _audit, _rb, applied = _make_trust(tmp_path)
    item_id = queue.propose("Open gate?", {"type": "switch", "rollback_key": "gate"})
    assert len(queue.items) == 1
    assert queue.items[0].id == item_id
    assert applied == []  # not auto-applied


def test_whitelisted_auto_applies(tmp_path):
    queue, _audit, _rb, applied = _make_trust(tmp_path)
    queue.propose(
        "Raise setpoint 2°C",
        {"type": "setpoint_adjust", "rollback_key": "climate.bedroom"},
    )
    assert len(queue.items) == 0
    assert len(applied) == 1


def test_confirm_yes_applies_no_rejects(tmp_path):
    queue, _audit, _rb, applied = _make_trust(tmp_path)
    a = queue.propose("A?", {"type": "x"})
    b = queue.propose("B?", {"type": "y"})
    assert queue.confirm(a, "yes") is True
    assert [act["type"] for act in applied] == ["x"]
    assert queue.confirm(b, "no") is True
    assert len(queue.items) == 0
    assert queue.confirm("missing", "yes") is False


def test_audit_log_append_only(tmp_path):
    queue, audit, _rb, _applied = _make_trust(tmp_path)
    item = queue.propose("A?", {"type": "x"})
    queue.confirm(item, "yes")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    import json

    events = [json.loads(line)["event"] for line in lines]
    assert events == ["queue.proposed", "action.applied"]
    assert audit.path.exists()


def test_rollback_restores_previous(tmp_path):
    queue, _audit, rollback, applied = _make_trust(tmp_path)
    queue.propose("Set 22", {"type": "setpoint", "rollback_key": "t", "previous": 20})
    item = queue.items[0]
    queue.confirm(item.id, "yes")
    assert rollback.previous("t") == 20
    assert queue.rollback_last("t") is True
    assert applied[-1] == {"type": "rollback", "rollback_key": "t", "value": 20}
    assert queue.rollback_last("unknown") is False


def test_propose_dedupes_same_action(tmp_path):
    """A proposal whose action is already queued returns the existing item."""
    queue, _audit, _rb, _applied = _make_trust(tmp_path)
    action = {"type": "supervisor_flag", "flag": "sensor_dead:x"}
    first = queue.propose("Supervisor: sensor_dead:x", action)
    again = queue.propose("Supervisor: sensor_dead:x", dict(action))
    assert len(queue.items) == 1
    assert again == first
    # A different action still queues normally.
    other = queue.propose(
        "Supervisor: gate_stuck:y", {"type": "supervisor_flag", "flag": "gate_stuck:y"}
    )
    assert len(queue.items) == 2
    assert other != first


def test_queue_item_serialization():
    item = QueueItem(id="abc", title="T", summary="S")
    assert item.as_dict()["id"] == "abc"
    assert item.as_dict()["title"] == "T"


def test_learning_stats_count_decisions(tmp_path):
    queue, _audit, _rb, _applied = _make_trust(tmp_path)
    a = queue.propose(
        "Setpoint?",
        {
            "type": "supervisor",
            "kind": "setpoint",
            "entity_id": "number.x",
            "value": 22,
        },
    )
    b = queue.propose(
        "Proposal?", {"type": "supervisor", "kind": "proposal", "title": "T"}
    )
    # Deduped re-propose must not inflate the counter.
    queue.propose(
        "Setpoint?",
        {
            "type": "supervisor",
            "kind": "setpoint",
            "entity_id": "number.x",
            "value": 22,
        },
    )
    queue.confirm(a, "yes")
    queue.confirm(b, "no")
    queue.propose("Silent", {"type": "setpoint_adjust", "rollback_key": "number.y"})
    learning = queue.as_learning()
    assert learning["total"] == {
        "proposed": 2,
        "accepted": 1,
        "rejected": 1,
        "applied_silent": 1,
    }
    assert learning["by_kind"] == {
        "number": {"accepted": 1, "rejected": 0},
        "proposal": {"accepted": 0, "rejected": 1},
    }


def test_reset_stats_keeps_queue(tmp_path):
    queue, _audit, _rb, _applied = _make_trust(tmp_path)
    item = queue.propose("A?", {"type": "x"})
    queue.confirm(item, "yes")
    assert queue.stats["accepted"] == 1
    queue.reset_stats()
    assert queue.as_learning() == {
        "total": dict.fromkeys(queue.stats, 0),
        "by_kind": {},
    }
    assert queue.stats["accepted"] == 0
