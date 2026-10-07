"""Tests for update-proofing: persistent queue, config migrations, core gate."""

from __future__ import annotations

import json
from pathlib import Path
import tarfile

from pilot_addon.coregate import (
    begin_update,
    complete_update,
    pending_update,
)
from pilot_addon.main import build_state
from pilot_addon.state import CONFIG_SCHEMA_VERSION, RuntimeState, migrate_config
from pilot_addon.trust import AuditLog, QueueItem, RollbackRegistry, TrustQueue

# -- persistent trust queue -------------------------------------------------------


def _queue_with_path(tmp_path: Path, **kwargs) -> TrustQueue:
    return TrustQueue(
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        rollback=RollbackRegistry(),
        apply_action=lambda action: None,
        path=tmp_path / "queue.json",
        **kwargs,
    )


def test_queue_survives_restart(tmp_path):
    queue = _queue_with_path(tmp_path)
    first = queue.propose("A?", {"type": "x", "flag": "a"})
    queue.propose("B?", {"type": "x", "flag": "b"})

    # A fresh instance over the same directory restores the pending items.
    reborn = _queue_with_path(tmp_path)
    assert [item.title for item in reborn.items] == ["A?", "B?"]
    assert reborn.items[0].action == {"type": "x", "flag": "a"}
    # And decisions keep working on the restored queue.
    assert reborn.confirm(first, "yes") is True
    assert len(reborn.items) == 1

    again = _queue_with_path(tmp_path)
    assert [item.title for item in again.items] == ["B?"]


def test_queue_persistence_atomic_and_tolerant(tmp_path):
    queue = _queue_with_path(tmp_path)
    queue.propose("A?", {"type": "x"})
    assert (tmp_path / "queue.json").exists()
    # Broken file on startup -> empty queue, no crash.
    (tmp_path / "queue.json").write_text("{broken", encoding="utf-8")
    reborn = _queue_with_path(tmp_path)
    assert reborn.items == []
    # Non-list garbage -> same.
    (tmp_path / "queue.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert _queue_with_path(tmp_path).items == []


def test_queue_clear_persists(tmp_path):
    queue = _queue_with_path(tmp_path)
    queue.propose("A?", {"type": "x"})
    queue.clear()
    assert _queue_with_path(tmp_path).items == []


def test_queue_restore_dedupes_ids(tmp_path):
    queue = _queue_with_path(tmp_path)
    queue.propose("A?", {"type": "x"})
    # Corrupted file with a duplicated record: restored once.
    record = queue.items[0].to_record()
    (tmp_path / "queue.json").write_text(
        json.dumps([record, dict(record)]), encoding="utf-8"
    )
    assert len(_queue_with_path(tmp_path).items) == 1


def test_queue_without_path_stays_memory_only(tmp_path):
    queue = TrustQueue(
        audit=AuditLog(tmp_path / "logs" / "audit.jsonl"),
        rollback=RollbackRegistry(),
        apply_action=lambda action: None,
    )
    queue.propose("A?", {"type": "x"})
    assert not (tmp_path / "queue.json").exists()


def test_build_state_restores_queue(tmp_path):
    state = build_state(tmp_path)
    state.queue.propose("Ждёт подтверждения", {"type": "x"})
    # Simulated restart: a new state over the same data dir.
    reborn = build_state(tmp_path)
    assert [item.title for item in reborn.queue.items] == ["Ждёт подтверждения"]


def test_queue_item_record_roundtrip():
    item = QueueItem(id="abc", title="T", summary="S", action={"k": 1}, created_ts=5.0)
    restored = QueueItem.from_record(item.to_record())
    assert restored == item


# -- config schema migrations ------------------------------------------------------


def test_migrate_config_stamps_v1(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.config_path.write_text(
        json.dumps(
            {"supervisor": {"base_url": "https://a/v1", "api_key": "k", "model": "m"}}
        ),
        encoding="utf-8",
    )
    assert state.migrate_config_file() is True
    saved = json.loads(state.config_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == CONFIG_SCHEMA_VERSION
    # Legacy fields untouched by the migration.
    assert saved["supervisor"]["model"] == "m"
    # Second run: nothing to do.
    assert state.migrate_config_file() is False


def test_read_config_applies_migration_in_memory(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.config_path.write_text(json.dumps({"supervisor": {}}), encoding="utf-8")
    assert state.read_config()["schema_version"] == CONFIG_SCHEMA_VERSION
    # Not persisted until an explicit write.
    assert "schema_version" not in json.loads(state.config_path.read_text())


def test_migrate_config_forward_compatible():
    future = {"schema_version": CONFIG_SCHEMA_VERSION + 5, "supervisor": {}}
    assert migrate_config(dict(future)) == future


def test_write_config_section_keeps_schema_version(tmp_path):
    state = RuntimeState(str(tmp_path))
    state.write_config_section("supervisor", {"base_url": "https://a/v1"})
    saved = json.loads(state.config_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == CONFIG_SCHEMA_VERSION


# -- core update gate ---------------------------------------------------------------


def _core_data(tmp_path: Path, version: str | None = "1.0.0") -> Path:
    core_dir = tmp_path / "openclaw"
    core_dir.mkdir()
    (core_dir / "memory").mkdir()
    (core_dir / "memory" / "facts.md").write_text(
        "дом: тепловой насос", encoding="utf-8"
    )
    (core_dir / "config.yaml").write_text("model: x", encoding="utf-8")
    if version is not None:
        (core_dir / ".core-version").write_text(version, encoding="utf-8")
    return core_dir


def test_gate_snapshots_before_migration(tmp_path):
    core_dir = _core_data(tmp_path, version="1.0.0")
    result = begin_update(tmp_path, "1.1.0")
    assert result["status"] == "snapshotted"
    assert result["from"] == "1.0.0" and result["to"] == "1.1.0"
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    with tarfile.open(snapshot) as tar:
        names = tar.getnames()
    assert "openclaw/memory/facts.md" in names
    assert "openclaw/.core-version" in names
    # Pending flag visible in the status surface.
    assert pending_update(tmp_path) == {"to": "1.1.0", "snapshot": str(snapshot)}
    # The original data is untouched so far.
    assert (core_dir / ".core-version").read_text() == "1.0.0"


def test_gate_completes_only_when_healthy(tmp_path):
    _core_data(tmp_path, version="1.0.0")
    begin_update(tmp_path, "1.1.0")
    # Failed health check: marker stays, flag stays.
    assert complete_update(tmp_path, "1.1.0", healthy=False) is False
    assert pending_update(tmp_path) is not None
    assert (tmp_path / "openclaw" / ".core-version").read_text() == "1.0.0"
    # Healthy: marker stamped, flag cleared, pending gone.
    assert complete_update(tmp_path, "1.1.0", healthy=True) is True
    assert pending_update(tmp_path) is None
    assert (tmp_path / "openclaw" / ".core-version").read_text() == "1.1.0"


def test_gate_inert_states(tmp_path):
    # No core data at all.
    assert begin_update(tmp_path, "1.1.0") == {"status": "none"}
    # Same version: fresh, nothing to do.
    _core_data(tmp_path, version="1.1.0")
    assert begin_update(tmp_path, "1.1.0")["status"] == "fresh"
    # No pinned version (env unset).
    assert begin_update(tmp_path, "") == {"status": "none"}


def test_gate_no_marker_treats_data_as_unknown(tmp_path):
    _core_data(tmp_path, version=None)
    result = begin_update(tmp_path, "2.0.0")
    assert result["status"] == "snapshotted"
    assert result["from"] == "unknown"


def test_snapshot_surfaces_in_status(tmp_path):
    state = build_state(tmp_path)
    _core_data(tmp_path, version="1.0.0")
    begin_update(tmp_path, "1.1.0")
    assert state.snapshot()["core_update"]["to"] == "1.1.0"
