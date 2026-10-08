"""The pre-gateway core preparation pass (coreprep)."""

from __future__ import annotations

from pilot_addon import coreprep, coresync


def test_coreprep_runs_all_steps_in_order(tmp_path, monkeypatch):
    calls: list[str] = []

    async def _ensure(state):
        calls.append("ensure")
        return ["plugins on"]

    async def _model(state):
        calls.append("model")
        return True

    async def _round():
        calls.append("round")
        return True

    monkeypatch.setattr(coresync, "ensure_runtime_config", _ensure)
    monkeypatch.setattr(coresync, "sync_model", _model)
    monkeypatch.setattr(coresync, "ensure_evening_round", _round)
    monkeypatch.setattr(
        coresync,
        "sync_persona",
        lambda state, language="ru": calls.append("persona") or True,
    )
    monkeypatch.setenv("PILOT_DATA", str(tmp_path))
    assert coreprep.main() == 0
    assert calls == ["ensure", "model", "persona", "round"]
