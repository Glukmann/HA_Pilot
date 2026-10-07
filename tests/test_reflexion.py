"""Cycle 4-5: weekly reflexion digest + accept-rate cadence calibration."""

from __future__ import annotations

import json
import time

from pilot_addon.norms import propose_norms, record_decision
from pilot_addon.reflexion import run_reflexion

from .test_addon_chat import _start
from .test_norms import NOW, _furnished, _record_regular


def _state_with_norm(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7, minute=2)
    return state


# -- reflexion ----------------------------------------------------------------------


async def test_reflexion_writes_weekly_digest(tmp_path, socket_enabled, monkeypatch):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = (
            "- Хозаин стабильно встаёт к 7:00.\n- Свет в холле — по расписанию."
        )
        state.vitrine.update(
            {
                "light.hall": {
                    "state": "on",
                    "attrs": {"friendly_name": "Свет холл"},
                    "area": "Холл",
                }
            }
        )
        _record_regular(state, "light.hall", days=6, hour=7, minute=2)

        result = await run_reflexion(state, now=NOW)
        assert result["ok"] is True
        journal = (tmp_path / "reflexion-log.md").read_text(encoding="utf-8")
        assert "## Неделя" in journal
        assert "по расписанию" in journal
        # Нормы и активность дошли до промпта.
        prompt = llm.requests[0]["messages"][1]["content"]
        assert "Выученные нормы" in prompt
        assert "light.hall: schedule_on около 07:0" in prompt
        assert "Решения хозяина" in prompt
        # Второй прогон той же недели — самоблокировка.
        assert await run_reflexion(state, now=NOW) == {"skipped": "done"}
    finally:
        await close()


async def test_reflexion_guards(tmp_path, socket_enabled, monkeypatch):
    state, _port, _llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        state.config_path.unlink(missing_ok=True)
        assert (await run_reflexion(state, now=NOW))["skipped"] == "no_config"

        state.write_config_section(
            "supervisor", {"base_url": "http://x/v1", "api_key": "k", "model": "m"}
        )
        state.set_mode("vacation")
        assert (await run_reflexion(state, now=NOW))["skipped"] == "mode"
    finally:
        await close()


# -- cadence calibration -------------------------------------------------------------


def _two_norm_state(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7, minute=2)
    state.vitrine.update(
        {
            "light.kitchen": {
                "state": "off",
                "attrs": {"friendly_name": "Кухня"},
                "area": "Кухня",
            }
        }
    )
    for i in range(6):
        ts = NOW - (i + 1) * 24 * 3600 + 8 * 3600
        state.events.record("light.kitchen", "on", ts)
    return state


def test_high_accept_rate_earns_second_daily_slot(tmp_path):
    state = _two_norm_state(tmp_path)
    for i in range(6):
        record_decision(
            state,
            {"type": "pattern", "kind": "schedule_on", "entity_id": f"light.p{i}"},
            "yes",
        )
    assert len(propose_norms(state, NOW)) == 1
    assert len(propose_norms(state, NOW)) == 1  # второй слот за день
    assert len(propose_norms(state, NOW)) == 0  # кап исчерпан


def test_low_accept_rate_throttles_to_every_second_day(tmp_path):
    state = _two_norm_state(tmp_path)
    for i in range(6):
        record_decision(
            state,
            {"type": "pattern", "kind": "schedule_on", "entity_id": f"light.p{i}"},
            "no",
        )
    assert len(propose_norms(state, NOW)) == 1
    # «Вчера» уже предлагали — сегодня rare-режим молчит.
    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert meta["proposals"]["day"] == time.strftime("%Y-%m-%d", time.localtime(NOW))
    assert propose_norms(state, NOW) == []
