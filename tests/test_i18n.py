"""Chat language: resolution order and per-language behavior of the fallback."""

from __future__ import annotations

from pilot_addon.chat import chat_ask
from pilot_addon.director import direct_answer
from pilot_addon.i18n import normalize_language, resolve_language
from pilot_addon.main import build_state

from .test_addon_chat import _start
from .test_director import _furnished_state, _sample


def test_language_normalization():
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("en") == "en"
    assert normalize_language("EN_us") == "en"
    assert normalize_language("fr-FR") == ""
    assert normalize_language(None) == ""
    assert normalize_language("") == ""


def test_language_resolution_order(tmp_path):
    state = build_state(tmp_path)
    # Request wins.
    assert resolve_language(state, "en-US") == "en"
    # Configured default applies when the request carries none.
    state.write_config_section("chat", {"language": "en"})
    assert resolve_language(state, None) == "en"
    # Russian is the last-resort default.
    state2 = build_state(tmp_path / "other")
    assert resolve_language(state2, None) == "ru"


def _english_state(tmp_path):
    state = build_state(tmp_path)
    state.vitrine.update(
        {
            "light.office_desk": _sample("on", "Desk light", area="Office"),
            "light.office_top": _sample("on", "Ceiling light", area="Office"),
            "sensor.office_air": _sample("22.1", "Office temperature", "°C", "Office"),
            "climate.bedroom": _sample("heat", "Bedroom climate", area="Bedroom"),
        }
    )
    return state


def test_fallback_english_lights(tmp_path):
    state = _english_state(tmp_path)
    result = direct_answer(state, "turn off the lights in the office", "en")
    assert result is not None
    assert [a["entity_id"] for a in result["actions"]] == [
        "light.office_desk",
        "light.office_top",
    ]
    assert "Office" in result["say"]


def test_fallback_english_temperature_honest(tmp_path):
    state = build_state(tmp_path)
    state.vitrine.update(
        {
            "sensor.boiler_probe": _sample("55", "Boiler probe", "°C", "Basement"),
            "sensor.router_radio": _sample(
                "41", "Router 5 GHz temperature", "°C", "Basement"
            ),
        }
    )
    result = direct_answer(state, "what's the temperature in the basement?", "en")
    assert result is not None
    assert "Boiler probe — 55 °C" in result["say"]
    assert "GHz" not in result["say"]

    empty = build_state(tmp_path / "other")
    empty.vitrine.update({"switch.pump": _sample("off", "Pump", area="Garage")})
    none_result = direct_answer(empty, "what is the temperature in the garage?", "en")
    assert none_result is not None
    assert "no air temperature sensor" in none_result["say"]


def test_fallback_english_setpoint(tmp_path):
    state = _english_state(tmp_path)
    result = direct_answer(state, "set 21 in the bedroom", "en")
    assert result is not None
    assert result["actions"][0]["data"] == {"temperature": 21.0}


async def test_chat_ask_english_prompt_and_fallback(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = '{"say": "Done.", "actions": []}'
        state.vitrine.update({"light.hall": _sample("on", "Hall light", area="Hall")})
        # A non-fallback ask in English: the EN system prompt and labels.
        await chat_ask(state, "how is the house doing?", language="en-US")
        system = llm.requests[0]["messages"][0]["content"]
        user = llm.requests[0]["messages"][1]["content"]
        assert "You are Pilot" in system
        assert "Home map (vitrine):" in user
        assert "Owner's message:" in user
        # An English simple ask is answered without the LLM.
        await chat_ask(state, "turn off the lights in the hall", language="en")
        assert len(llm.requests) == 1  # still a single LLM call total
    finally:
        await close()


def test_russian_still_default(tmp_path, socket_enabled, monkeypatch):
    # resolve without language and without config -> ru table in the fallback
    state = _furnished_state(tmp_path)
    result = direct_answer(state, "выключи свет в кабинете")
    assert result is not None
    assert "Выключаю" in result["say"]
