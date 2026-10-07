"""Deterministic chat fallback: simple asks answered without any LLM call.

Recognised shapes (per the language tables in i18n.py, deliberately
narrow — anything else goes to the LLM):

- «(выключи|включи|переключи) свет в <комнате>» / "turn off the lights in
  <room>" -> light turn_on/off/toggle for every light in that area
  (safety.classify re-checks each action);
- «какая температура в <комнате>?» / "what's the temperature in <room>?"
  -> every °C reading in the area with its device name, device-internal
  probes (router radio, chips) excluded — or an honest "no air sensor
  here" instead of a name-similarity guess;
- «поставь 22 [градуса] в <комнате>» / "set 22 in <room>" ->
  climate.set_temperature.

Returns the same contract as chat_ask ({"say", "actions", "cost": 0,
"fallback": True}) or None when not confident. Zero tokens.
"""

from __future__ import annotations

import re
from typing import Any

from .i18n import table
from .safety import classify
from .state import RuntimeState

# Device-internal readings that must never answer for room climate.
_DIAGNOSTIC_MARKERS = (
    "ггц",
    "ghz",
    "чип",
    "chip",
    "slzb",
    "keenetic",
    "роутер",
    "router",
    "cpu",
    "процессор",
    "аптайм",
    "uptime",
    "nvme",
    "ssd",
    "диск",
    "dbm",
    "_rx",
    "_tx",
    "-rx",
    "-tx",
)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def _match_area(state: RuntimeState, message: str) -> str | None:
    """Find a vitrine area whose name (or its stem) occurs in the message."""
    haystack = _norm(message)
    areas = {
        str(sample.get("area"))
        for sample in state.vitrine.states.values()
        if isinstance(sample, dict) and sample.get("area")
    }
    best: str | None = None
    for area in sorted(areas):
        name = _norm(area)
        variants = [name]
        for cut in (1, 2, 3):
            if len(name) - cut >= 4:
                variants.append(name[:-cut])
        for variant in variants:
            if variant and variant in haystack:
                if best is None or len(variant) > len(_norm(best)):
                    best = area
                break
    return best


def _area_samples(state: RuntimeState, area: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (eid, sample)
        for eid, sample in sorted(state.vitrine.states.items())
        if isinstance(sample, dict) and sample.get("area") == area
    ]


def _is_diagnostic(eid: str, sample: dict[str, Any]) -> bool:
    name = str(sample.get("attrs", {}).get("friendly_name") or "")
    haystack = f"{eid} {name}".lower().replace("ё", "е")
    return any(marker in haystack for marker in _DIAGNOSTIC_MARKERS)


def _lights(state: RuntimeState, area: str) -> list[str]:
    return [
        eid for eid, sample in _area_samples(state, area) if eid.startswith("light.")
    ]


def _temp_readings(state: RuntimeState, area: str) -> list[tuple[str, str]]:
    """(display name, value) of °C readings in the area, devices excluded."""
    readings: list[tuple[str, str]] = []
    for eid, sample in _area_samples(state, area):
        if not eid.startswith("sensor."):
            continue
        attrs_raw = sample.get("attrs")
        attrs = attrs_raw if isinstance(attrs_raw, dict) else {}
        if attrs.get("unit_of_measurement") not in ("°C", "°F"):
            continue
        if _is_diagnostic(eid, sample):
            continue
        name = str(attrs.get("friendly_name") or eid)
        readings.append((name, f"{sample.get('state', '?')} °C"))
    return readings


def _climates(state: RuntimeState, area: str) -> list[str]:
    return [
        eid for eid, sample in _area_samples(state, area) if eid.startswith("climate.")
    ]


def direct_answer(
    state: RuntimeState, message: str, language: str = "ru"
) -> dict[str, Any] | None:
    """Parse a simple ask; None means 'not confident, hand to the LLM'."""
    t = table(language)
    text = _norm(message).rstrip("?!.")
    if t["light_noun"] in text:
        for verb, service in t["light_verbs"]:
            if text.startswith(verb):
                answer = _lights_answer(state, text, service, t)
                if answer is not None:
                    return answer
    if t["temp_word"] in text and text.startswith(t["temp_starts"]):
        return _temperature_answer(state, text, t)
    for verb in t["set_verbs"]:
        if text.startswith(verb):
            answer = _setpoint_answer(state, text, t)
            if answer is not None:
                return answer
    return None


def _lights_answer(
    state: RuntimeState,
    text: str,
    service: str,
    t: dict[str, Any],
) -> dict[str, Any] | None:
    area = _match_area(state, text)
    if area is None:
        return None  # «выключи свет» без комнаты — неоднозначно, пусть LLM
    lights = _lights(state, area)
    if not lights:
        return {
            "say": t["say"]["lights_none"].format(area=area),
            "actions": [],
            "cost": 0.0,
            "fallback": True,
        }
    actions = [
        {
            "domain": "light",
            "service": service,
            "entity_id": eid,
            "data": {},
            "mode": classify({"domain": "light", "service": service, "entity_id": eid}),
        }
        for eid in lights
    ]
    if any(a["mode"] != "direct" for a in actions):
        return None  # safety layer disagrees — escalate to the LLM path
    return {
        "say": t["say"][service].format(area=area, count=len(lights)),
        "actions": actions,
        "cost": 0.0,
        "fallback": True,
    }


def _temperature_answer(
    state: RuntimeState, text: str, t: dict[str, Any]
) -> dict[str, Any] | None:
    area = _match_area(state, text)
    if area is None:
        return None
    readings = _temp_readings(state, area)
    if not readings:
        return {
            "say": t["say"]["temp_none"].format(area=area),
            "actions": [],
            "cost": 0.0,
            "fallback": True,
        }
    listing = "; ".join(f"{name} — {value}" for name, value in readings)
    return {
        "say": t["say"]["temp_listing"].format(area=area, listing=listing),
        "actions": [],
        "cost": 0.0,
        "fallback": True,
    }


def _setpoint_answer(
    state: RuntimeState, text: str, t: dict[str, Any]
) -> dict[str, Any] | None:
    match = re.match(t["setpoint_re"], text)
    if match is None:
        return None
    raw_value = match.group("value")
    raw_area = match.group("area")
    area = _match_area(state, raw_area) if raw_area else None
    if area is None:
        return None
    climates = _climates(state, area)
    if len(climates) != 1:
        return None  # ни одного или несколько — неоднозначно, пусть LLM
    value = raw_value.replace(",", ".")
    entity_id = climates[0]
    action = {
        "domain": "climate",
        "service": "set_temperature",
        "entity_id": entity_id,
        "data": {"temperature": float(value)},
        "mode": classify(
            {
                "domain": "climate",
                "service": "set_temperature",
                "entity_id": entity_id,
            }
        ),
    }
    if action["mode"] != "direct":
        return None
    return {
        "say": t["say"]["setpoint"].format(area=area, value=value),
        "actions": [action],
        "cost": 0.0,
        "fallback": True,
    }
