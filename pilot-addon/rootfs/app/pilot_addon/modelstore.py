"""Model profiles: several LLM configs, one active.

Profiles live in the supervisor config section:

    "supervisor": {
        "schedule": "07:00",
        "models": [
            {"id": "deepseek", "label": "DeepSeek",
             "base_url": "https://api.deepseek.com/v1",
             "api_key": "sk-...", "model": "deepseek-chat",
             "price_input_per_1m": 15.0, "price_output_per_1m": 60.0}
        ],
        "active_id": "deepseek"
    }

Legacy top-level base_url/api_key/model stays the fallback: when no
profiles exist (or none is active), resolution returns the section as-is.
API keys are never exposed — lists carry ``has_key`` only, and an upsert
with the mask value ("***") keeps the stored key.
"""

from __future__ import annotations

import re
import time
from typing import Any

_KEY_MASK = "***"
_PROFILE_FIELDS = (
    "base_url",
    "api_key",
    "model",
    "price_input_per_1m",
    "price_output_per_1m",
)


class ModelError(ValueError):
    """Invalid profile input; message is owner-facing (ws error payload)."""


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or time.strftime("%H%M%S")


def profiles_of(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("models")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def active_id_of(section: dict[str, Any]) -> str | None:
    raw = section.get("active_id")
    if not isinstance(raw, str) or not raw:
        return None
    return raw if any(p["id"] == raw for p in profiles_of(section)) else None


def resolve_section(section: dict[str, Any]) -> dict[str, Any]:
    """Supervisor config with the active profile overlaid (legacy fallback)."""
    active_id = active_id_of(section)
    if active_id is None:
        return dict(section)
    profile = next(p for p in profiles_of(section) if p["id"] == active_id)
    resolved = dict(section)
    for field in _PROFILE_FIELDS:
        if profile.get(field) is not None:
            resolved[field] = profile[field]
    return resolved


def masked_items(section: dict[str, Any]) -> list[dict[str, Any]]:
    """Profile list for the UI: no api_key values, has_key instead."""
    items: list[dict[str, Any]] = []
    for profile in profiles_of(section):
        items.append(
            {
                "id": profile["id"],
                "label": str(profile.get("label") or profile["id"]),
                "base_url": str(profile.get("base_url") or ""),
                "model": str(profile.get("model") or ""),
                "has_key": bool(profile.get("api_key")),
                "price_input_per_1m": profile.get("price_input_per_1m"),
                "price_output_per_1m": profile.get("price_output_per_1m"),
            }
        )
    return items


def _validated(payload: dict[str, Any]) -> dict[str, Any]:
    base_url = str(payload.get("base_url") or "").strip()
    if not re.match(r"^https?://", base_url):
        raise ModelError("base_url должен начинаться с http:// или https://")
    model = str(payload.get("model") or "").strip()
    if not model:
        raise ModelError("Укажите модель")
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise ModelError("Укажите API-ключ")

    def _price(key: str) -> float | None:
        raw = payload.get(key)
        if raw in (None, ""):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError) as err:
            raise ModelError(f"{key} должен быть числом") from err
        if value < 0:
            raise ModelError(f"{key} не может быть отрицательным")
        return value

    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "label": str(payload.get("label") or "").strip() or model,
        "price_input_per_1m": _price("price_input_per_1m"),
        "price_output_per_1m": _price("price_output_per_1m"),
    }


def upsert(
    section: dict[str, Any], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Create or update a profile; "***" api_key keeps the stored one.

    Returns (new_models_list, profile_id).
    """
    profiles = [dict(p) for p in profiles_of(section)]
    raw_id = str(payload.get("id") or "").strip()
    existing = next((p for p in profiles if p["id"] == raw_id), None)

    values = _validated(payload)
    if values["api_key"] == _KEY_MASK:
        if existing is None or not existing.get("api_key"):
            raise ModelError("Ключ не сохранён — введите настоящий")
        values["api_key"] = existing["api_key"]

    if existing is None:
        base = _slug(str(payload.get("label") or values["model"]))
        candidate, counter = base, 2
        while any(p["id"] == candidate for p in profiles):
            candidate = f"{base}-{counter}"
            counter += 1
        profile = {"id": candidate, **values}
        profiles.append(profile)
        return profiles, candidate

    existing.update(values)
    return profiles, str(existing["id"])


def without(section: dict[str, Any], profile_id: str) -> list[dict[str, Any]]:
    """Remove a profile; the active one cannot be removed."""
    if active_id_of(section) == profile_id:
        raise ModelError("Активную модель нельзя удалить — сначала переключитесь")
    profiles = profiles_of(section)
    if not any(p["id"] == profile_id for p in profiles):
        raise ModelError(f"unknown model: {profile_id}")
    return [p for p in profiles if p["id"] != profile_id]
