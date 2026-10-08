"""Norm cycles 1-2: event journal + deterministic norm detector -> queue."""

from __future__ import annotations

import json
import time

from pilot_addon.eventlog import EventLog
from pilot_addon.main import build_state
from pilot_addon.norms import detect_norms, propose_deviations, propose_norms

NOW = time.mktime((2026, 10, 7, 12, 0, 0, 0, 0, -1))


def _record_regular(
    state, eid: str, days: int, hour: int = 7, minute: int = 2, spread_min: int = 5
):
    """``days`` ON events over the trailing week at ~hour:minute."""
    for i in range(days):
        day = NOW - (i + 1) * 24 * 3600
        lt = time.localtime(day)
        ts = time.mktime(
            (
                lt.tm_year,
                lt.tm_mon,
                lt.tm_mday,
                hour,
                minute + (i % 2) * spread_min,
                0,
                0,
                0,
                -1,
            )
        )
        state.events.record(eid, "on", ts)


def _furnished(tmp_path):
    state = build_state(tmp_path)
    state.vitrine.update(
        {
            "light.hall": {
                "state": "off",
                "attrs": {"friendly_name": "Свет холл"},
                "area": "Холл",
            }
        }
    )
    return state


# -- event journal -----------------------------------------------------------------


def test_event_log_records_and_filters(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.record("light.hall", "on", NOW - 10 * 24 * 3600)
    log.record("light.hall", "off", NOW - 2 * 24 * 3600)
    log.record("light.hall", "on", NOW - 3600)
    recent = log.transitions(NOW - 3 * 24 * 3600)
    assert [r["state"] for r in recent] == ["off", "on"]
    assert log.transitions(NOW + 100) == []


def test_event_log_rotation_drops_old(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, max_age_days=7)
    log.record("light.hall", "on", NOW - 20 * 24 * 3600)
    log.record("light.hall", "off", NOW - 3600)
    kept = log.rotate(NOW)
    assert kept == 1
    assert [r["state"] for r in log.transitions(0)] == ["off"]


def test_push_vitrine_journals_only_real_transitions(tmp_path):
    state = _furnished(tmp_path)
    state.push_vitrine(  # первый пуш — переход unknown -> on
        {"light.hall": {"state": "on", "attrs": {"friendly_name": "Свет холл"}}}
    )
    state.push_vitrine(  # то же состояние — не переход
        {"light.hall": {"state": "on", "attrs": {"friendly_name": "Свет холл"}}}
    )
    state.push_vitrine({"light.hall": {"state": "off", "attrs": {}}})
    records = state.events.transitions(0)
    assert [r["state"] for r in records] == ["on", "off"]


# -- norm detection ------------------------------------------------------------------


def test_detects_stable_morning_norm(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7, minute=2)
    candidates = detect_norms(state, NOW)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["entity_id"] == "light.hall"
    assert candidate["kind"] == "schedule_on"
    assert candidate["time"] in ("07:02", "07:04", "07:07")
    assert candidate["days"] >= 5


def test_unstable_times_are_not_a_norm(tmp_path):
    state = _furnished(tmp_path)
    for i in range(6):
        _record_regular(state, "light.hall", days=1, hour=(6 + i * 3) % 24, minute=0)
    assert detect_norms(state, NOW) == []


def test_sparse_days_are_not_a_norm(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=3, hour=7)
    assert detect_norms(state, NOW) == []


def test_midnight_norm_is_circular(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=23, minute=50)
    candidates = detect_norms(state, NOW)
    assert len(candidates) == 1  # разброс через полночь не ломает норму
    assert candidates[0]["time"].startswith("23:5")


# -- proposals ------------------------------------------------------------------------


def test_norm_becomes_queue_proposal(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7, minute=2)
    proposed = propose_norms(state, NOW)
    assert len(proposed) == 1
    item = state.queue.items[0]
    assert "Норма" in item.title
    assert "Свет холл" in item.title
    assert "07:0" in item.title
    assert item.action == {
        "type": "pattern",
        "kind": "schedule_on",
        "entity_id": "light.hall",
        "time": item.action["time"],
    }
    # Суть предложения явна: рецепт автоматизации и последствия да/нет.
    assert "триггер — время" in item.summary
    assert "light.turn_on(light.hall)" in item.summary
    assert "«Да»" in item.summary and "«Нет»" in item.summary
    # Дедуп: повторный прогон не плодит копии.
    assert propose_norms(state, NOW) == []
    assert len(state.queue.items) == 1


def test_daily_cap_allows_one_proposal(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7)
    _record_regular(state, "light.kitchen", days=6, hour=8) if False else None
    state.vitrine.update(
        {
            "light.kitchen": {
                "state": "off",
                "attrs": {"friendly_name": "Кухня свет"},
                "area": "Кухня",
            }
        }
    )
    state.events.record("light.kitchen", "on", NOW - 6 * 24 * 3600 + 8 * 3600)
    state.events.record("light.kitchen", "on", NOW - 5 * 24 * 3600 + 8 * 3600)
    state.events.record("light.kitchen", "on", NOW - 4 * 24 * 3600 + 8 * 3600)
    state.events.record("light.kitchen", "on", NOW - 3 * 24 * 3600 + 8 * 3600)
    state.events.record("light.kitchen", "on", NOW - 2 * 24 * 3600 + 8 * 3600)
    state.events.record("light.kitchen", "on", NOW - 1 * 24 * 3600 + 8 * 3600)
    assert len(propose_norms(state, NOW)) == 1
    # Второй кандидат сегодня — мимо: кап «одна норма в день».
    assert propose_norms(state, NOW) == []


def test_learning_pauses_outside_normal_mode(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7)
    state.set_mode("vacation")
    assert propose_norms(state, NOW) == []
    assert state.queue.items == []


def test_two_declines_silence_the_category(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7)
    # Два отклонённых предложения, которые детектор знает как «свои».
    (tmp_path / "norms.json").write_text(
        json.dumps({"proposed_titles": ["Норма A", "Норма B"]}), encoding="utf-8"
    )
    for title in ("Норма A", "Норма B"):
        item = state.queue.propose(
            title,
            {
                "type": "pattern",
                "kind": "schedule_on",
                "entity_id": "light.a",
                "time": "07:00",
            },
        )
        state.queue.confirm(item, "no")
    assert propose_norms(state, NOW) == []


def test_two_other_declines_do_not_silence(tmp_path):
    """Отказы на чужие (не нормы) предложения гвард не считает."""
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7)
    for _ in range(2):
        item = state.queue.propose("Чужое предложение", {"type": "supervisor"})
        state.queue.confirm(item, "no")
    assert len(propose_norms(state, NOW)) == 1


def test_norm_meta_persisted(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7)
    propose_norms(state, NOW)
    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert meta["last_proposal_day"] == time.strftime("%Y-%m-%d", time.localtime(NOW))


def test_norms_csv_complete_for_supported_languages():
    from pilot_addon import i18n
    from pilot_addon.norms import _norm_rows

    rows = _norm_rows()
    for language in i18n.supported_languages():
        missing = [
            k for k, cells in rows.items() if not (cells.get(language) or "").strip()
        ]
        assert missing == [], f"norms.csv: {language} misses {missing}"


# -- deviations ---------------------------------------------------------------------


def _flagged_state(tmp_path):
    state = _furnished(tmp_path)
    state.flags = ["light_always_on:light.hall", "sensor_dead:sensor.x"]
    return state


def test_deviation_flag_becomes_neutralise_proposal(tmp_path):
    state = _flagged_state(tmp_path)
    proposed = propose_deviations(state, NOW)
    assert len(proposed) == 1
    item = state.queue.items[0]
    assert "горит всю ночь" in item.title
    assert "Свет холл" in item.title
    assert item.action == {
        "type": "deviation",
        "domain": "light",
        "service": "turn_off",
        "entity_id": "light.hall",
        "data": {},
    }
    # Дедуп: тот же флаг снова — без новых предложений.
    assert propose_deviations(state, NOW) == []
    assert len(state.queue.items) == 1


def test_gate_flag_neutralise_proposal(tmp_path):
    state = _furnished(tmp_path)
    state.vitrine.update(
        {
            "switch.gate": {
                "state": "on",
                "attrs": {"friendly_name": "Калитка"},
                "area": "Двор",
            }
        }
    )
    state.flags = ["gate_stuck:switch.gate"]
    proposed = propose_deviations(state, NOW)
    assert len(proposed) == 1
    assert "Калитка" in state.queue.items[0].title
    assert state.queue.items[0].action["service"] == "turn_off"


def test_deviation_daily_cap_and_mode_guard(tmp_path):
    state = _flagged_state(tmp_path)
    assert len(propose_deviations(state, NOW)) == 1
    state.queue.items.clear()  # «другой день» той же ситуации
    assert propose_deviations(state, NOW) == []  # кап на сегодня исчерпан
    reborn = _flagged_state(tmp_path / "other")
    reborn.set_mode("guests")
    assert propose_deviations(reborn, NOW) == []
