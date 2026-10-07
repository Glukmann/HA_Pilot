"""Decision memory: accepted/declined norms and deviations never repeat."""

from __future__ import annotations

import json

from pilot_addon.main import build_state
from pilot_addon.norms import propose_deviations, propose_norms, record_decision

from .test_norms import NOW, _furnished, _record_regular


def _norm_state(tmp_path):
    state = _furnished(tmp_path)
    _record_regular(state, "light.hall", days=6, hour=7, minute=2)
    return state


def test_declined_norm_never_proposed_again(tmp_path):
    state = _norm_state(tmp_path)
    _proposed = propose_norms(state, NOW)
    item = state.queue.items[0]
    assert state.queue.confirm(item.id, "no") is True

    # «Другой день», та же норма — память решения её глушит.
    (tmp_path / "norms.json").write_text(
        json.dumps(
            {
                **json.loads((tmp_path / "norms.json").read_text(encoding="utf-8")),
                "last_proposal_day": "2000-01-01",
            }
        ),
        encoding="utf-8",
    )
    assert propose_norms(state, NOW) == []
    assert state.queue.items == []


def test_accepted_norm_never_proposed_again(tmp_path):
    state = _norm_state(tmp_path)
    record_decision(
        state,
        {"type": "pattern", "kind": "schedule_on", "entity_id": "light.hall"},
        "yes",
    )
    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert meta["decided"]["accepted"] == ["light.hall:schedule_on"]
    assert propose_norms(state, NOW) == []


def test_declined_deviation_remembers(tmp_path):
    state = _furnished(tmp_path)
    state.flags = ["light_always_on:light.hall"]
    _proposed = propose_deviations(state, NOW)
    item = state.queue.items[0]
    state.queue.confirm(item.id, "no")

    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert meta["decided"]["declined"] == ["light.hall:turn_off"]
    # Сбрасываем дневной кап — память всё равно молчит.
    meta["last_deviation_day"] = "2000-01-01"
    (tmp_path / "norms.json").write_text(json.dumps(meta), encoding="utf-8")
    assert propose_deviations(state, NOW) == []


def test_foreign_actions_do_not_pollute_memory(tmp_path):
    state = build_state(tmp_path)
    record_decision(
        state,
        {
            "type": "chat",
            "domain": "light",
            "service": "turn_off",
            "entity_id": "light.hall",
        },
        "no",
    )
    record_decision(
        state,
        {"type": "supervisor", "kind": "proposal", "title": "x"},
        "no",
    )
    from pilot_addon.norms import _load_meta

    # Чужие действия память не трогают — файла меты даже не появилось.
    assert _load_meta(state) == {}


def test_decision_hook_wired_in_build_state(tmp_path):
    state = _norm_state(tmp_path)
    _proposed = propose_norms(state, NOW)
    item = state.queue.items[0]
    # Хук срабатывает сам через queue.confirm — память на диске.
    state.queue.confirm(item.id, "no")
    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert "light.hall:schedule_on" in meta["decided"]["declined"]


def test_decision_memory_capped(tmp_path):
    state = build_state(tmp_path)
    for i in range(250):
        record_decision(
            state,
            {"type": "pattern", "kind": "schedule_on", "entity_id": f"light.e{i}"},
            "no",
        )
    meta = json.loads((tmp_path / "norms.json").read_text(encoding="utf-8"))
    assert len(meta["decided"]["declined"]) == 200
