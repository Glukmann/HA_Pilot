"""OpenClaw core update gate (docs/2026-10-07-openclaw-update-policy.md).

The add-on image pins the core version (OPENCLAW_VERSION build arg), so a
core upgrade only happens with an add-on rebuild. The core's data home —
its config, sessions and memory — lives in /data/openclaw and survives
rebuilds on the Supervisor volume. The dangerous moment is the core's own
schema migration after a version jump.

Gate:
1. ``begin_update`` at container start: when the pinned version differs
   from the marker the previous run left, snapshot /data/openclaw into a
   timestamped tarball BEFORE the core gets a chance to migrate, and leave
   a ``.migration-pending`` flag with the target version.
2. ``complete_update`` after the health check: only when healthy, stamp
   the new marker and clear the flag. A failed migration keeps the old
   marker and the flag — the snapshot stands ready for a manual restore,
   and the status surface shows the incident.

While the core is not yet bundled (ENV pin without payload), the gate is
inert: with no /data/openclaw directory it reports "none".
"""

from __future__ import annotations

import logging
from pathlib import Path
import tarfile
import time
from typing import Any

logger = logging.getLogger("pilot.addon")

CORE_DIR_NAME = "openclaw"
MARKER_NAME = ".core-version"
PENDING_NAME = ".migration-pending"


def _core_dir(data_dir: Path) -> Path:
    return data_dir / CORE_DIR_NAME


def _read_marker(core_dir: Path) -> str | None:
    try:
        text = (core_dir / MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def pending_update(data_dir: Path) -> dict[str, Any] | None:
    """The unfinished migration, if any: {"to": version, "snapshot": path}."""
    flag = _core_dir(data_dir) / PENDING_NAME
    try:
        target = flag.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not target:
        return None
    snapshots = sorted(data_dir.glob(f"{CORE_DIR_NAME}.bak-*-to-{target}-*.tar.gz"))
    return {"to": target, "snapshot": str(snapshots[-1]) if snapshots else None}


def begin_update(data_dir: Path, core_version: str) -> dict[str, Any]:
    """Snapshot the core data before a version jump; no-op when none needed."""
    if not core_version:
        return {"status": "none"}
    core_dir = _core_dir(data_dir)
    if not core_dir.is_dir() or not any(core_dir.iterdir()):
        return {"status": "none"}
    current = _read_marker(core_dir)
    if current == core_version:
        return {"status": "fresh", "version": current}
    from_version = current or "unknown"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    snapshot = (
        data_dir
        / f"{CORE_DIR_NAME}.bak-{from_version}-to-{core_version}-{stamp}.tar.gz"
    )
    try:
        with tarfile.open(snapshot, "w:gz") as tar:
            tar.add(core_dir, arcname=CORE_DIR_NAME)
    except OSError:
        logger.exception("core update gate: snapshot failed")
        return {"status": "error", "error": "snapshot failed"}
    (core_dir / PENDING_NAME).write_text(core_version, encoding="utf-8")
    logger.info(
        "core update gate: %s -> %s, snapshot %s",
        from_version,
        core_version,
        snapshot.name,
    )
    return {
        "status": "snapshotted",
        "from": from_version,
        "to": core_version,
        "snapshot": str(snapshot),
    }


def complete_update(data_dir: Path, core_version: str, healthy: bool) -> bool:
    """Stamp the new marker only when the migration health check passed."""
    core_dir = _core_dir(data_dir)
    flag = core_dir / PENDING_NAME
    if not flag.exists():
        return False
    if not healthy:
        logger.warning(
            "core update gate: health check failed, marker stays; "
            "restore from snapshot if needed"
        )
        return False
    (core_dir / MARKER_NAME).write_text(core_version, encoding="utf-8")
    flag.unlink(missing_ok=True)
    logger.info("core update gate: migration to %s completed and stamped", core_version)
    return True
