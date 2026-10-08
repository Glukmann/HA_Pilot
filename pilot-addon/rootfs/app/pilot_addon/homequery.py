"""Read-only queries into Home Assistant for the agent's tools.

The vitrine is the home's overview; these endpoints let the agent go
deeper when it is not enough: a fresh reading of one entity, its change
history (trends, turn-on times) and recorder statistics (energy sums).
All read-only — the trust contour is untouched.
"""

from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_HISTORY_POINTS = 120


def _ha_base() -> str:
    return os.environ.get("HA_API_BASE", "http://supervisor/core/api")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}"}


async def ha_get(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """GET the HA Core API (Supervisor proxy); returns (status, json|None)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_ha_base()}{path}",
            params=params,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            try:
                return resp.status, await resp.json()
            except (aiohttp.ClientError, json.JSONDecodeError):
                return resp.status, None


async def ha_post(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_ha_base()}{path}",
            json=payload,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            try:
                return resp.status, await resp.json()
            except (aiohttp.ClientError, json.JSONDecodeError):
                return resp.status, None


def compact_history(raw: Any, hours: float) -> dict[str, Any]:
    """HA /api/history response -> compact change list + numeric summary."""
    if not isinstance(raw, list) or not raw:
        return {"hours": hours, "points": [], "summary": None}
    entries: list[dict[str, Any]] = []
    for batch in raw:
        if isinstance(batch, list):
            entries.extend(e for e in batch if isinstance(e, dict))
    points = [
        [
            str(e.get("last_changed") or ""),
            str(e.get("state") or ""),
        ]
        for e in entries[-MAX_HISTORY_POINTS:]
    ]
    summary = None
    numeric = []
    for e in entries:
        try:
            numeric.append(float(str(e.get("state"))))
        except (TypeError, ValueError):
            numeric = []
            break
    if numeric:
        summary = {
            "first": numeric[0],
            "last": numeric[-1],
            "min": min(numeric),
            "mean": round(sum(numeric) / len(numeric), 3),
            "max": max(numeric),
        }
    return {"hours": hours, "points": points, "summary": summary}
