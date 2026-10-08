"""One-shot core preparation — runs BEFORE the gateway starts (s6).

The gateway refuses to become ready when its config changes during startup,
so all config convergence (plugin/tools/hooks/heartbeat, model profile,
persona files, evening automation) must settle while the gateway is still
down. This module is that pre-flight pass; the long-running Python service
keeps only the cheap hot-reload-safe resyncs (persona drift, ws triggers).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

logger = logging.getLogger("pilot.coreprep")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    from . import coresync
    from .main import build_state

    data_dir = Path(os.environ.get("PILOT_DATA", "/data"))
    state = build_state(data_dir)

    # Diagnostics (no secrets): why a profile may be missing on this host.
    from .modelstore import active_id_of, profiles_of

    raw = state.read_config() or {}
    section = raw.get("supervisor")
    profiles = profiles_of(section) if isinstance(section, dict) else []
    logger.info(
        "pilot.json: schema=%s supervisor=%s profiles=%d active=%s",
        raw.get("schema_version"),
        "present" if isinstance(section, dict) else "MISSING",
        len(profiles),
        active_id_of(section) if isinstance(section, dict) else None,
    )

    applied = asyncio.run(coresync.ensure_runtime_config(state))
    logger.info("runtime config: %s", ", ".join(applied) or "already converged")
    model_synced = asyncio.run(coresync.sync_model(state))
    logger.info(
        "model sync: %s", "applied" if model_synced else "no active profile (skipped)"
    )
    persona_changed = coresync.sync_persona(state)
    logger.info("persona files: %s", "written" if persona_changed else "unchanged")
    evening_ok = asyncio.run(coresync.ensure_evening_round())
    logger.info(
        "evening round: %s", "ensured" if evening_ok else "not ensured (will retry)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
