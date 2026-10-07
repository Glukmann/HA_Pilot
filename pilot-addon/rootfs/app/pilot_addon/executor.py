"""Action executor: the trust queue's hands in Home Assistant.

The trust layer decides WHAT may happen; this module performs the confirmed
action. Today the only executable actions are setpoint writes
(number.* / input_number.*, the same family the supervisor's ±3 whitelist
uses) plus rollbacks of those writes. Everything else is refused or reduced
to an acknowledgement — irreversible actions never reach HA from here.

Every outcome lands in the audit log via the caller (main._run_action):
applied / acknowledged / refused / error (with re-queue for retry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

import aiohttp

HA_API_BASE = os.environ.get("HA_API_BASE", "http://supervisor/core/api")
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
EXECUTABLE_PREFIXES = ("number.", "input_number.")


class ExecutorError(Exception):
    """The HA call failed; the caller decides whether to re-queue."""


@dataclass
class ExecResult:
    """Outcome of one executor attempt (never raises for policy decisions)."""

    status: str  # "applied" | "ack" | "refused"
    detail: dict[str, Any] = field(default_factory=dict)


async def ha_set_value(
    session: aiohttp.ClientSession,
    entity_id: str,
    value: float,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> None:
    """Write a number/input_number value through the HA Core API."""
    domain = entity_id.split(".", 1)[0]
    url = f"{base_url or HA_API_BASE}/services/{domain}/set_value"
    auth = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
    headers = {"Authorization": f"Bearer {auth}"}
    async with session.post(
        url,
        json={"entity_id": entity_id, "value": value},
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise ExecutorError(f"HA HTTP {resp.status}")


class HaExecutor:
    """Executes confirmed trust-queue actions against the HA Core API."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self._base_url = base_url
        self._token = token

    async def apply(self, action: dict[str, Any]) -> ExecResult:
        """Perform one action. Raises ExecutorError only on HA transport/HTTP.

        Policy outcomes (nothing to do, unsupported or unsafe action) are
        returned as ExecResult, not raised — they are recorded, not retried.
        """
        if action.get("kind") == "proposal":
            # Informational proposal: the owner's "yes" is the decision itself.
            return ExecResult("ack", {"title": str(action.get("title", ""))})
        target = self._resolve(action)
        if target is None:
            return ExecResult("refused", {"reason": "unsupported_action"})
        entity_id, value = target
        if not entity_id.startswith(EXECUTABLE_PREFIXES):
            return ExecResult(
                "refused",
                {"reason": "domain_not_executable", "entity_id": entity_id},
            )
        async with aiohttp.ClientSession() as session:
            await ha_set_value(
                session,
                entity_id,
                value,
                base_url=self._base_url,
                token=self._token,
            )
        return ExecResult("applied", {"entity_id": entity_id, "value": value})

    @staticmethod
    def _resolve(action: dict[str, Any]) -> tuple[str, float] | None:
        """Map an action to (entity_id, value); None when not executable."""
        if action.get("kind") == "setpoint":
            entity_id, raw = action.get("entity_id"), action.get("value")
        elif action.get("type") == "rollback":
            key = str(action.get("rollback_key") or "")
            if not key.startswith("setpoint:"):
                return None
            entity_id, raw = key.split(":", 1)[1], action.get("value")
        else:
            return None
        if not isinstance(entity_id, str) or not entity_id:
            return None
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return None
        return entity_id, float(raw)
