"""Cycle 1.5: the deterministic chat fallback (0-token simple asks)."""

from __future__ import annotations

from pilot_addon.chat import chat_ask
from pilot_addon.director import direct_answer
from pilot_addon.main import build_state

from .test_addon_chat import _start


def _sample(state: str, name: str, unit=None, area="Кабинет") -> dict:
    attrs = {"friendly_name": name}
    if unit:
        attrs["unit_of_measurement"] = unit
    return {"state": state, "attrs": attrs, "area": area}


def _furnished_state(tmp_path):
    state = build_state(tmp_path)
    state.vitrine.update(
        {
            "light.cabinet_desk": _sample("on", "Свет стол"),
            "light.cabinet_top": _sample("on", "Свет верх"),
            "sensor.cabinet_air": _sample("23.5", "Кабинет t°", "°C"),
            "sensor.router_5g_temp": _sample("41", "Keenetic Температура 5 ГГц", "°C"),
            "sensor.cabinet_battery": _sample("96", "Кабинет Батарея", "%"),
            "climate.bedroom": _sample("heat", "Спальня климат", area="Спальня"),
            "light.hall": _sample("off", "Свет холл", area="Холл"),
        }
    )
    return state


def test_light_off_in_room(tmp_path):
    state = _furnished_state(tmp_path)
    result = direct_answer(state, "выключи свет в кабинете")
    assert result is not None and result["cost"] == 0.0
    assert result["fallback"] is True
    assert [a["entity_id"] for a in result["actions"]] == [
        "light.cabinet_desk",
        "light.cabinet_top",
    ]
    assert all(a["service"] == "turn_off" for a in result["actions"])
    assert all(a["mode"] == "direct" for a in result["actions"])
    assert "Выключаю свет" in result["say"]


def test_light_in_empty_room_is_honest(tmp_path):
    state = _furnished_state(tmp_path)
    result = direct_answer(state, "выключи свет в спальне")
    assert result is not None
    assert result["actions"] == []  # в спальне климат, но нет света
    assert "нет световых устройств" in result["say"]


def test_light_without_room_is_not_confident(tmp_path):
    state = _furnished_state(tmp_path)
    assert direct_answer(state, "выключи свет") is None  # LLM path
    assert direct_answer(state, "привет") is None
    assert direct_answer(state, "что у нас с домом?") is None


def test_temperature_query_lists_real_readings_only(tmp_path):
    state = _furnished_state(tmp_path)
    result = direct_answer(state, "какая температура в кабинете?")
    assert result is not None
    # Router radio probe excluded; battery excluded; air sensor listed.
    assert "Кабинет t° — 23.5 °C" in result["say"]
    assert "ГГц" not in result["say"]


def test_temperature_query_no_sensor_is_honest(tmp_path):
    state = build_state(tmp_path)
    state.vitrine.update({"switch.pump": _sample("off", "Насос", area="Подвал")})
    result = direct_answer(state, "какая температура в подвале?")
    assert result is not None
    assert result["actions"] == []
    assert "Датчика температуры воздуха" in result["say"]


def test_temperature_query_without_room_goes_to_llm(tmp_path):
    state = _furnished_state(tmp_path)
    assert direct_answer(state, "какая температура?") is None


def test_setpoint_single_climate(tmp_path):
    state = _furnished_state(tmp_path)
    result = direct_answer(state, "поставь 22 в спальне")
    assert result is not None
    action = result["actions"][0]
    assert action["domain"] == "climate"
    assert action["service"] == "set_temperature"
    assert action["data"] == {"temperature": 22.0}
    assert "22" in result["say"]


def test_setpoint_no_climate_in_room_is_not_confident(tmp_path):
    state = _furnished_state(tmp_path)
    assert direct_answer(state, "поставь 22 в кабинете") is None


async def test_chat_ask_uses_fallback_without_llm(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        state.vitrine.update({"light.hall": _sample("on", "Свет холл", area="Холл")})
        result = await chat_ask(state, "выключи свет в холле", conversation_id="c-1")
        assert result["fallback"] is True
        assert result["cost"] == 0.0
        assert result["actions"][0]["entity_id"] == "light.hall"
        assert llm.requests == []  # ни одного LLM-вызова
        # И ход попал в историю сессии.
        assert state.sessions.history("c-1")[0]["text"] == "выключи свет в холле"
    finally:
        await close()
