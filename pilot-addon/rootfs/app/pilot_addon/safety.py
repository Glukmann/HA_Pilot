"""Deterministic action classification for the chat remote control.

The LLM proposes, this module disposes — 0 tokens, hard rules (trust core):

- ``direct``: reversible services on the whitelist (lights, media, climate
  setpoints, …) — executed immediately by the HA integration;
- ``queue``: allowed but needs owner confirmation (gates/doors/locks and
  gate-marked entities, covers, unknown domains and services) — lands in the
  trust queue;
- ``refuse``: never executed (cameras, alarms — chat is not a surveillance
  console).
"""

from __future__ import annotations

from typing import Any

from .checker import GATE_MARKERS

# Reversible, everyday services: fine to run the moment the owner asks.
SAFE_SERVICES: dict[str, frozenset[str]] = {
    "light": frozenset({"turn_on", "turn_off", "toggle"}),
    "media_player": frozenset(
        {
            "media_play",
            "media_pause",
            "media_stop",
            "media_next_track",
            "media_previous_track",
            "volume_set",
            "volume_up",
            "volume_down",
        }
    ),
    "fan": frozenset({"turn_on", "turn_off"}),
    "humidifier": frozenset({"turn_on", "turn_off"}),
    "climate": frozenset({"set_temperature"}),
    "water_heater": frozenset({"set_temperature"}),
    "number": frozenset({"set_value"}),
    "input_number": frozenset({"set_value"}),
    "input_boolean": frozenset({"turn_on", "turn_off", "toggle"}),
    "scene": frozenset({"turn_on"}),
}

# Never from chat, whatever the LLM says.
REFUSE_DOMAINS = frozenset({"camera", "alarm_control_panel", "lock"})


def classify(action: dict[str, Any]) -> str:
    """Return "direct" | "queue" | "refuse" for one proposed service call."""
    domain = str(action.get("domain") or "")
    service = str(action.get("service") or "")
    entity_id = str(action.get("entity_id") or "")
    if domain in REFUSE_DOMAINS:
        return "refuse"
    if entity_id.split(".", 1)[0] in REFUSE_DOMAINS:
        return "refuse"
    allowed = SAFE_SERVICES.get(domain)
    if allowed is None or service not in allowed:
        return "queue"
    if _looks_like_gate(entity_id):
        # A switch-like entity named gate/door (e.g. a relay) is never direct.
        return "queue"
    return "direct"


def _looks_like_gate(entity_id: str) -> bool:
    lowered = entity_id.lower()
    return any(marker in lowered for marker in GATE_MARKERS)
