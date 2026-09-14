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


def test_queue_item_serialization():
    item = QueueItem(id="abc", title="T", summary="S")
    assert item.as_dict()["id"] == "abc"
    assert item.as_dict()["title"] == "T"
