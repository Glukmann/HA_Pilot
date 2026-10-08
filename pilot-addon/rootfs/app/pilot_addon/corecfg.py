"""First-boot seeding of the OpenClaw core config (merge stage 1).

The core's home lives at /data/openclaw (OPENCLAW_STATE_DIR). On the very
first start we write a minimal openclaw.json — loopback-only gateway, token
auth, hooks and cron enabled, no channels yet (stage 5) — and stamp the
.core-version marker so the update gate (coregate.py) knows the baseline.
Existing configs are never overwritten: the owner's edits and the core's
own migrations win.
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
from typing import Any

STATE_DIR_NAME = "openclaw"
CONFIG_NAME = "openclaw.json"
GATEWAY_PORT = 18789


def state_dir(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_DIR_NAME


def config_path(data_dir: Path) -> Path:
    return state_dir(data_dir) / CONFIG_NAME


def _default_config() -> dict[str, Any]:
    return {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": GATEWAY_PORT,
            "auth": {"mode": "token", "token": secrets.token_urlsafe(32)},
        },
        "hooks": {
            "enabled": True,
            "path": "/hooks",
            "token": secrets.token_urlsafe(32),
        },
        "cron": {"enabled": True},
    }


def seed_config(data_dir: Path, core_version: str = "") -> bool:
    """Create the core config when absent; True only on a fresh seed."""
    cfg_path = config_path(data_dir)
    if cfg_path.exists():
        return False
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_name(CONFIG_NAME + ".tmp")
    tmp.write_text(
        json.dumps(_default_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(cfg_path)
    if core_version:
        (cfg_path.parent / ".core-version").write_text(
            core_version, encoding="utf-8"
        )
    return True


if __name__ == "__main__":
    import os

    seed_config(
        Path(os.environ.get("PILOT_DATA", "/data")),
        core_version=os.environ.get("OPENCLAW_VERSION", ""),
    )
