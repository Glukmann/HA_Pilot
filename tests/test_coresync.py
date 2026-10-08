"""Tests for core config/persona sync."""

from __future__ import annotations

import json

from pilot_addon import coresync
from pilot_addon.corecfg import state_dir
from pilot_addon.main import build_state


async def test_ensure_runtime_config_sets_everything(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def _fake_config_set(args):
        calls.append(args)
        return True, ""

    monkeypatch.setattr(coresync, "config_set", _fake_config_set)
    state = build_state(tmp_path)
    applied = await coresync.ensure_runtime_config(state)
    keys = [".".join(c[:1]) for c in calls]
    flat = "\n".join(" ".join(c) for c in calls)
    assert any("plugins.load.paths" in k for k in keys)
    assert any("plugins.allow" in k for k in keys)
    assert any("tools.allow" in k for k in keys)
    assert any("tools.deny" in k for k in keys)
    assert any("allowedSessionKeyPrefixes" in k for k in keys)
    assert any("heartbeat" in k for k in keys)
    assert '"pilot-home"' in flat
    assert "vitrine_get" in flat and "home_action" in flat
    assert len(applied) == len(calls)


async def test_sync_model_writes_provider_and_default(tmp_path, monkeypatch):
    calls = []

    async def _fake(args):
        calls.append(args)
        return True, ""

    monkeypatch.setattr(coresync, "config_set", _fake)
    (tmp_path / "pilot.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "supervisor": {
                    "base_url": "https://llm.example/v1",
                    "api_key": "k",
                    "model": "deepseek-chat",
                },
            }
        ),
        encoding="utf-8",
    )
    state = build_state(tmp_path)
    assert await coresync.sync_model(state) is True
    provider_call = next(c for c in calls if c[0] == "models.providers.pilot")
    provider = json.loads(provider_call[1])
    assert provider["baseUrl"] == "https://llm.example/v1"
    assert provider["apiKey"] == "k"
    assert provider["models"] == [{"id": "deepseek-chat"}]
    default_call = next(c for c in calls if c[0] == "agents.defaults.model")
    assert default_call[1] == '"pilot/deepseek-chat"'


async def test_sync_model_without_profile_is_noop(tmp_path, monkeypatch):
    async def _boom(args):  # pragma: no cover - must never be called
        raise AssertionError("config_set must not run without a profile")

    monkeypatch.setattr(coresync, "config_set", _boom)
    state = build_state(tmp_path)
    assert await coresync.sync_model(state) is False


def test_sync_persona_writes_workspace_files(tmp_path):
    state = build_state(tmp_path)
    assert coresync.sync_persona(state) is True
    workspace = state_dir(tmp_path) / "workspace"
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
    identity = (workspace / "IDENTITY.md").read_text(encoding="utf-8")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Пилот" in soul and "butler" not in soul  # пресета переведена
    assert "Дворецкий" in soul
    assert "Пилот" in identity
    assert "home_action" in agents_md and "vitrine_get" in agents_md
    # Идемпотентность: повторный вызов ничего не меняет.
    assert coresync.sync_persona(state) is False
    # Изменение персоны перезаписывает.
    state.set_persona("verbosity", 90)
    assert coresync.sync_persona(state) is True
    assert "разговорчивость 90" in (workspace / "SOUL.md").read_text(encoding="utf-8")
