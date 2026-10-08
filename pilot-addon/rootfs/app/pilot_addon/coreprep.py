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
    logger.info("build marker: coreprep-0202")  # deploy-pipeline canary

    # Ground-truth resolution diagnostics (no secrets) — independent of
    # whatever coresync build ships in this image.
    from .modelstore import resolve_section

    resolved = resolve_section(section if isinstance(section, dict) else {})
    first_keys = sorted(profiles[0].keys()) if profiles else []
    logger.info(
        "profile resolution: base_url=%s model=%s api_key=%s | first profile keys=%s",
        bool(resolved.get("base_url")),
        resolved.get("model") or "<empty>",
        bool(resolved.get("api_key")),
        first_keys,
    )
    model_synced = asyncio.run(coresync.sync_model(state))
    logger.info(
        "model sync: %s", "applied" if model_synced else "FAILED (see warning above)"
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
