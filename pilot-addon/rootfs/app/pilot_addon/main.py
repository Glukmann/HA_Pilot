"""Entry point of the Pilot add-on runtime.

Composition: HTTP API (the integration contract) + deterministic checker
loop + daily LLM supervisor run. The vitrine is fed by pushes from the HA
integration. The supervisor (docs/2026-09-13-supervisor-design.md) reasons
over checker flags once a day: whitelisted setpoints apply silently,
everything else lands in the trust queue for the owner.
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
from .executor import ExecutorError, HaExecutor
from .http_api import create_app
from .promptstore import seed_defaults
from .state import RuntimeState
from .supervisor import run_supervisor, schedule_hhmm, seconds_until
from .trust import WHITELISTED_ACTIONS, AuditLog, RollbackRegistry, TrustQueue

DATA_DIR = Path(os.environ.get("PILOT_DATA", "/data"))

# Strong references to fire-and-forget executor tasks, so a running task
# is not garbage-collected mid-flight (RUF006).
_pending_tasks: set[asyncio.Task[None]] = set()

logger = logging.getLogger("pilot.addon")


def build_state(
    data_dir: Path = DATA_DIR, executor: HaExecutor | None = None
) -> RuntimeState:
    """Compose runtime state with the trust layer attached."""
    state = RuntimeState(str(data_dir), token=os.environ.get("PILOT_TOKEN", ""))
    if budget := os.environ.get("PILOT_DAILY_BUDGET"):
        state.daily_budget = float(budget)
    audit = AuditLog(data_dir / "logs" / "audit.jsonl")
    rollback = RollbackRegistry()
    ha_executor = executor or HaExecutor()

    def _apply_action(action: dict[str, object]) -> None:
        # Confirmed (or whitelisted) actions are executed asynchronously;
        # without a running loop there is no one to execute them.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            audit.record(
                "action.executor.skipped",
                {"action": action, "reason": "no_event_loop"},
            )
            return
        task = loop.create_task(_run_action(state, ha_executor, audit, action))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)

    def _whitelisted(action: dict[str, object]) -> bool:
        # A failed retry comes back to the owner, never auto-applies again.
        return not action.get("retry") and action.get("type") in WHITELISTED_ACTIONS

    state.attach_trust(
        TrustQueue(
            audit=audit,
            rollback=rollback,
            apply_action=_apply_action,
            whitelisted=_whitelisted,
            path=data_dir / "queue.json",
        )
    )
    return state


async def _run_action(
    state: RuntimeState,
    executor: HaExecutor,
    audit: AuditLog,
    action: dict[str, object],
) -> None:
    """Execute one confirmed action; outcome goes to the audit log.

    On an HA failure the action returns to the queue marked ``retry`` so
    the owner sees it still needs attention and confirms the retry —
    it will not auto-apply again (see ``_whitelisted`` above).
    """
    try:
        result = await executor.apply(action)
    except (ExecutorError, aiohttp.ClientError, TimeoutError) as err:
        audit.record("action.executor.error", {"action": action, "error": str(err)})
        state.queue.propose(
            title=(
                "⚠️ Не исполнено: "
                f"{action.get('title') or action.get('entity_id') or 'действие'}"
            ),
            summary=f"Ошибка исполнения в HA: {err}. Подтвердите повторно.",
            action={**action, "retry": True},
        )
        return
    audit.record(
        f"action.executor.{result.status}",
        {"action": action, **result.detail},
    )


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


async def norm_loop(state: RuntimeState, interval_s: float = 1800) -> None:
    """Habit learning: norm candidates -> trust proposals; never raises.

    Runs every 30 minutes but the guards (home mode, decline history,
    one-proposal-a-day cap) keep the owner unsolicited most of the time.
    """
    from .norms import propose_norms

    while True:
        try:
            propose_norms(state)
        except Exception:
            logger.exception("norm detection failed")  # soft degradation
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
    try:
        seed_defaults(DATA_DIR)
    except OSError:
        logger.exception("failed to seed prompt/skill defaults")
    try:
        if state.migrate_config_file():
            logger.info("config schema migrated to disk")
    except OSError:
        logger.exception("config schema migration failed")
    from .coregate import begin_update, complete_update

    core_version = os.environ.get("OPENCLAW_VERSION", "")
    gate = begin_update(DATA_DIR, core_version)
    checker = Checker()
    state.attach_checker(checker)

    # The vitrine is fed by pushes from the HA integration (vitrine_push) —
    # the integration IS HA, so no second WebSocket reader lives here.
    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(checker_loop(state, checker)),
        asyncio.create_task(norm_loop(state)),
    ]

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

    # The add-on itself is up — that is the health signal available until
    # the core ships in the image; its own ping wires into this same gate.
    if gate.get("status") == "snapshotted":
        complete_update(DATA_DIR, core_version, healthy=True)

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
