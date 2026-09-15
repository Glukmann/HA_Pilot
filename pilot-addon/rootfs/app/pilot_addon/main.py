"""Entry point of the Pilot add-on runtime.

Composition: HTTP API (the integration contract) + vitrine mirror +
daily supervisor scheduler. The LLM supervisor run is Phase 4 scope: the
scheduler fires once a day and records the run intent; the OpenClaw agent
execution wires in with the full workspace (Phase 4 hardening).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from .checker import Checker
from .discovery import publish_discovery
from .http_api import create_app
from .state import RuntimeState
from .trust import AuditLog, RollbackRegistry, TrustQueue
from .vitrine import VitrineMirror

DATA_DIR = Path(os.environ.get("PILOT_DATA", "/data"))

logger = logging.getLogger("pilot.addon")


def build_state(data_dir: Path = DATA_DIR) -> RuntimeState:
    """Compose runtime state with the trust layer attached."""
    state = RuntimeState(str(data_dir), token=os.environ.get("PILOT_TOKEN", ""))
    if budget := os.environ.get("PILOT_DAILY_BUDGET"):
        state.daily_budget = float(budget)
    audit = AuditLog(data_dir / "logs" / "audit.jsonl")
    rollback = RollbackRegistry()

    def _apply_action(action: dict[str, object]) -> None:
        # Whitelisted setpoint adjustments land here (Phase 4 wires HA calls).
        audit.record("action.executor", {"action": action, "note": "executor stub"})

    state.attach_trust(
        TrustQueue(audit=audit, rollback=rollback, apply_action=_apply_action)
    )
    return state


async def run_supervisor_tick(state: RuntimeState, checker: Checker) -> None:
    """Daily supervisor run: budget guard, then flags -> queue proposals."""
    if state.cost_today >= state.daily_budget:
        state.queue._audit.record("supervisor.skipped", {"reason": "budget"})
        return
    for flag in state.flags:
        state.queue.propose(
            title=f"Supervisor: {flag}",
            action={"type": "supervisor_flag", "flag": flag},
            summary="Daily supervisor proposal",
        )
    state.queue._audit.record("supervisor.tick", {"flags": len(state.flags)})


async def daily_scheduler(state: RuntimeState, checker: Checker, hour: int = 7) -> None:
    """Fire the supervisor once a day at the given local hour."""
    while True:
        now = asyncio.get_event_loop().time()
        # Simple interval-based approximation; a production cron pins the hour.
        await asyncio.sleep(24 * 3600 - (now % (24 * 3600)))
        await run_supervisor_tick(state, checker)


async def main() -> None:
    """Start HTTP API + vitrine mirror + scheduler."""
    logging.basicConfig(
        level=os.environ.get("PILOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = build_state()
    checker = Checker()
    state.attach_checker(checker)

    ha_ws = os.environ.get("HA_WS", "")
    ha_token = os.environ.get("HA_TOKEN", "")
    tasks: list[asyncio.Task[None]] = []
    if ha_ws:
        mirror = VitrineMirror(
            state,
            ws_url=ha_ws,
            token=ha_token,
            out_file=DATA_DIR / "vitrine.txt",
        )
        tasks.append(asyncio.create_task(mirror.run()))
    tasks.append(asyncio.create_task(daily_scheduler(state, checker)))

    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8899")))
    await site.start()
    logger.info(
        "Pilot add-on %s listening on port %s",
        state.runtime_version,
        os.environ.get("PORT", "8899"),
    )

    async with aiohttp.ClientSession() as session:
        await publish_discovery(session, port=int(os.environ.get("PORT", "8899")))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
