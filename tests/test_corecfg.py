"""Tests for the OpenClaw core config seeding (merge stage 1)."""

from __future__ import annotations

import json
from pathlib import Path

from pilot_addon.corecfg import config_path, seed_config, state_dir


def test_seed_creates_config_and_marker(tmp_path: Path):
    assert seed_config(tmp_path, core_version="2026.9.8") is True
    cfg = json.loads(config_path(tmp_path).read_text(encoding="utf-8"))
    assert cfg["gateway"]["mode"] == "local"
    assert cfg["gateway"]["bind"] == "loopback"
    assert cfg["gateway"]["port"] == 18789
    assert cfg["gateway"]["auth"]["mode"] == "token"
    assert len(cfg["gateway"]["auth"]["token"]) >= 32
    assert cfg["hooks"]["enabled"] is True
    assert len(cfg["hooks"]["token"]) >= 32
    assert cfg["cron"]["enabled"] is True
    assert "channels" not in cfg  # каналы — этап 5
    marker = (state_dir(tmp_path) / ".core-version").read_text(encoding="utf-8")
    assert marker == "2026.9.8"


def test_seed_is_idempotent_and_preserves_edits(tmp_path: Path):
    assert seed_config(tmp_path, core_version="2026.9.8") is True
    cfg_path = config_path(tmp_path)
    edited = json.loads(cfg_path.read_text(encoding="utf-8"))
    edited["gateway"]["port"] = 18799
    cfg_path.write_text(json.dumps(edited), encoding="utf-8")
    assert seed_config(tmp_path, core_version="2026.9.8") is False
    kept = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert kept["gateway"]["port"] == 18799


def test_seed_never_touches_broken_config(tmp_path: Path):
    state_dir(tmp_path).mkdir(parents=True)
    config_path(tmp_path).write_text("{broken", encoding="utf-8")
    assert seed_config(tmp_path, core_version="2026.9.8") is False
    assert config_path(tmp_path).read_text(encoding="utf-8") == "{broken"
