"""Announce the Pilot service to Home Assistant via Supervisor discovery.

The add-on ships with the `discovery: ["pilot"]` permission; a single POST to
the Supervisor is enough for HA to offer "Discovered Pilot — Confirm" in the
UI, with no host/port typing. On a standalone instance (no SUPERVISOR_TOKEN)
this is a silent no-op, so one image serves both deployment models.
"""

from __future__ import annotations

import logging
import os
import socket

import aiohttp

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"


async def publish_discovery(session: aiohttp.ClientSession, port: int) -> None:
    """Publish the pilot service to HA through the Supervisor.

    Never raises: a failed announcement must not block the runtime — the
    integration can always be set up manually by address.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        _LOGGER.info("No SUPERVISOR_TOKEN — standalone mode, skipping discovery")
        return
    payload = {
        "service": "pilot",
        "config": {"host": socket.gethostname(), "port": port},
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with session.post(
            f"{SUPERVISOR_URL}/discovery",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 201):
                _LOGGER.info("Announced Pilot to Home Assistant via Supervisor")
            else:
                _LOGGER.warning(
                    "Discovery announce failed: %s %s", resp.status, await resp.text()
                )
    except aiohttp.ClientError as err:
        _LOGGER.warning("Discovery announce error: %s", err)
