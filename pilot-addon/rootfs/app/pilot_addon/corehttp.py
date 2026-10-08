"""Tiny health client for the bundled OpenClaw core (merge stage 1).

The gateway listens on loopback only; /healthz is unauthenticated there.
Every failure mode (connection refused, timeout, HTTP error) maps to
alive=False — the add-on must degrade softly when the core is down.
"""

from __future__ import annotations

from typing import Any

import aiohttp

CORE_BASE = "http://127.0.0.1:18789"


async def core_health(
    session: aiohttp.ClientSession,
    base: str = CORE_BASE,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Probe the core gateway; never raises."""
    try:
        async with session.get(
            f"{base}/healthz", timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status == 200:
                return {"alive": True, "detail": "ok"}
            return {"alive": False, "detail": f"http {resp.status}"}
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        return {"alive": False, "detail": str(err)[:200]}
