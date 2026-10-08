"""Bridge to the bundled OpenClaw core: owner chat + config management.

Chat goes through the gateway's OpenAI-compatible endpoint (a full agent
run: memory, sessions, the pilot-home tools) and — unlike /hooks/agent —
its response carries real token usage, so the budget guard accounts actual
spend against the active model profile's prices.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .corecfg import GATEWAY_PORT
from .modelstore import resolve_section

CHAT_URL = f"http://127.0.0.1:{GATEWAY_PORT}/v1/chat/completions"


def _cost_from_usage(profile: dict[str, Any], usage: dict[str, Any]) -> float:
    """₽ from token usage and the active profile's per-million prices."""
    try:
        input_price = float(profile.get("input_price") or 0.0)
        output_price = float(profile.get("output_price") or 0.0)
        prompt = float(usage.get("prompt_tokens") or 0.0)
        completion = float(usage.get("completion_tokens") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return prompt / 1_000_000 * input_price + completion / 1_000_000 * output_price


def _active_profile(state: Any) -> dict[str, Any]:
    raw = state.read_config() or {}
    section = raw.get("supervisor")
    return resolve_section(section if isinstance(section, dict) else {})


async def ask_core(
    state: Any,
    message: str,
    conversation_id: str | None = None,
    language: str | None = "ru",
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """One owner turn through the core agent.

    Returns {"ok", "reply", "error", "cost"}; never raises. The budget
    guard runs BEFORE the call; spend accrues from real usage.
    """
    state.reset_cost_if_new_day()
    if state.cost_today >= state.daily_budget:
        return {"ok": False, "reply": "", "error": "budget", "cost": 0.0}
    profile = _active_profile(state)
    if not profile.get("base_url") or not profile.get("model"):
        return {"ok": False, "reply": "", "error": "not_configured", "cost": 0.0}
    session_key = f"pilot:{conversation_id or 'assist'}"[:64]
    payload = {
        "model": "openclaw",
        "user": session_key,
        "messages": [{"role": "user", "content": message}],
    }
    headers = {"X-OpenClaw-Session": session_key}
    if language:
        headers["Accept-Language"] = language
    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession()
    try:
        async with session.post(
            CHAT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                return {
                    "ok": False,
                    "reply": "",
                    "error": f"core http {resp.status}",
                    "cost": 0.0,
                }
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        return {"ok": False, "reply": "", "error": str(err)[:200], "cost": 0.0}
    finally:
        if owns_session:
            await session.close()
    choices = data.get("choices")
    reply = ""
    if isinstance(choices, list) and choices:
        reply = str(choices[0].get("message", {}).get("content") or "").strip()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = _cost_from_usage(profile, usage)
    if cost:
        state.accrue_cost(cost)
    return {"ok": True, "reply": reply, "error": "", "cost": cost}


async def core_cli(args: list[str], timeout_s: float = 20.0) -> tuple[bool, str]:
    """Run an `openclaw …` CLI against the core home; never raises."""
    import os

    env = dict(os.environ)
    env["OPENCLAW_STATE_DIR"] = env.get("PILOT_DATA", "/data") + "/openclaw"
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        return proc.returncode == 0, out.decode(errors="replace")[-1000:]
    except (OSError, TimeoutError) as err:
        return False, str(err)[:200]


async def config_set(args: list[str]) -> tuple[bool, str]:
    """Run `openclaw config set …` against the core home; never raises."""
    return await core_cli(["config", "set", *args])


# -- heartbeat cost accounting -------------------------------------------------
# The silent heartbeat (target:none) never reports usage; the budget guard
# accrues one honest estimate per day the core is alive. Real figures arrive
# when supervisor/rounds move to webhook-delivered runs (merge stage 4).
HEARTBEAT_RUNS_PER_DAY = 8  # every 2h within 08:00-23:00
HEARTBEAT_RUN_ESTIMATE = 0.2  # ₽ per light-context run


def accrue_heartbeat_estimate(state: Any) -> bool:
    """Accrue the daily heartbeat estimate once; True when accrued."""
    if not state.core_alive:
        return False
    state.reset_cost_if_new_day()
    if state.heartbeat_estimated_day == state.cost_day:
        return False
    state.heartbeat_estimated_day = state.cost_day
    state.accrue_cost(HEARTBEAT_RUNS_PER_DAY * HEARTBEAT_RUN_ESTIMATE)
    return True
