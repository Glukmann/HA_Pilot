"""Entry point of the Pilot add-on runtime.

Composition: HTTP API (the integration contract) + vitrine mirror +
deterministic checker loop + daily LLM supervisor run. The supervisor
(docs/2026-09-13-supervisor-design.md) reasons over checker flags once a
day: whitelisted setpoints apply silently, everything else lands in the
trust queue for the owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
from pathlib import Path
import time
from typing import Any

import aiohttp
from aiohttp import web

from .checker import Checker, EntitySample
from .discovery import publish_discovery
from .http_api import create_app
from .state import RuntimeState
from .supervisor import run_supervisor, schedule_hhmm, seconds_until
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


async def run_checker_pass(state: RuntimeState, checker: Checker) -> list[str]:
    """One checker iteration: vitrine -> deviation flags (state.flags).

    The flag list is fully replaced on every pass — a flag disappears as
    soon as its condition goes away. Called by the periodic loop and by
    tests directly (no sleeping involved).
    """
    now = time.time()
    samples: list[EntitySample] = []
    for eid, sample in state.vitrine.states.items():
        if not isinstance(sample, dict):
            continue
        try:
            changed_ts = float(sample.get("last_changed") or now)
        except (TypeError, ValueError):
            changed_ts = now
        attrs = sample.get("attrs")
        samples.append(
            EntitySample(
                entity_id=eid,
                state=str(sample.get("state", "")),
                attrs=attrs if isinstance(attrs, dict) else {},
                last_changed_ts=changed_ts,
            )
        )
    flags = checker.flags_for(samples)
    state.flags = flags
    state.queue._audit.record("checker.run", {"flags": len(flags), "flag_list": flags})
    return flags


async def checker_loop(
    state: RuntimeState, checker: Checker, interval_s: float = 300
) -> None:
    """Run the deterministic checker periodically; never raises."""
    while True:
        try:
            await run_checker_pass(state, checker)
        except Exception:
            logger.exception("checker pass failed")  # soft degradation
        await asyncio.sleep(interval_s)


async def supervisor_scheduler(
    state: RuntimeState,
    get_broadcast: Callable[[], Any] | None = None,
) -> None:
    """Fire the LLM supervisor daily at the configured local time.

    Never raises: a failed run is logged and retried the next day.
    """
    while True:
        hh, mm = schedule_hhmm(state)
        await asyncio.sleep(seconds_until(hh, mm))
        try:
            broadcast = get_broadcast() if get_broadcast else None
            await run_supervisor(state, broadcast=broadcast)
        except Exception:
            logger.exception("supervisor run failed")  # soft degradation


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
    tasks.append(asyncio.create_task(checker_loop(state, checker)))

    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8899")))
    await site.start()
    logger.info(
        "Pilot add-on %s (build ws.1) listening on port %s",
        state.runtime_version,
        os.environ.get("PORT", "8899"),
    )

    tasks.append(
        asyncio.create_task(
            supervisor_scheduler(state, lambda: app.get("ws_broadcast"))
        )
    )

    async with aiohttp.ClientSession() as session:
        await publish_discovery(session, port=int(os.environ.get("PORT", "8899")))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
