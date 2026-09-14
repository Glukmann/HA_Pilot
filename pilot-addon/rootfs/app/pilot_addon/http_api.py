"""HTTP API of the Pilot add-on — the contract the HA integration speaks.

Endpoints (see custom_components/pilot/api.py):
    GET  /api/validate
    GET  /api/status
    POST /api/persona      {slider, value}
    POST /api/budget       {value}
    POST /api/preset       {preset}
    POST /api/mode         {mode}
    POST /api/focus        {focus}
    POST /api/reset        {target: learning | all}
    GET  /api/queue
    POST /api/queue/confirm {id, decision}
    GET  /api/vitrine
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from .state import RuntimeState


def _auth_ok(request: web.Request, state: RuntimeState) -> bool:
    """Bearer token check; open when no token configured (trusted local net)."""
    if not state.token:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {state.token}"


def create_app(state: RuntimeState) -> web.Application:
    """Build the aiohttp application serving the contract."""
    app = web.Application()
    app["state"] = state

    async def validate(request: web.Request) -> web.Response:
        if not _auth_ok(request, state):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"ok": True})

    async def status(request: web.Request) -> web.Response:
        return web.json_response(state.snapshot())

    async def persona(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            state.set_persona(str(body["slider"]), int(body["value"]))
        except (KeyError, ValueError) as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response({"ok": True})

    async def budget(request: web.Request) -> web.Response:
        body = await request.json()
        state.daily_budget = float(body["value"])
        return web.json_response({"ok": True})

    async def preset(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            state.apply_preset(str(body["preset"]))
        except (KeyError, ValueError) as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response({"ok": True})

    async def mode(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            state.set_mode(str(body["mode"]))
        except (KeyError, ValueError) as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response({"ok": True})

    async def focus(request: web.Request) -> web.Response:
        body = await request.json()
        state.current_focus = str(body.get("focus", ""))
        return web.json_response({"ok": True})

    async def reset(request: web.Request) -> web.Response:
        body = await request.json()
        target = str(body.get("target", ""))
        if target == "learning":
            state.current_focus = ""
        elif target == "all":
            state.current_focus = ""
            state.cost_today = 0.0
            state.flags.clear()
            state.queue.items.clear()
        else:
            return web.json_response({"error": "unknown target"}, status=400)
        state.queue._audit.record("reset", {"target": target})
        return web.json_response({"ok": True})

    async def queue_get(request: web.Request) -> web.Response:
        return web.json_response(
            {"items": [item.as_dict() for item in state.queue.items]}
        )

    async def queue_confirm(request: web.Request) -> web.Response:
        body = await request.json()
        ok = state.queue.confirm(str(body["id"]), str(body["decision"]))
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"ok": True})

    async def vitrine(request: web.Request) -> web.Response:
        return web.json_response(state.vitrine.as_dict())

    app.router.add_get("/api/validate", validate)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/persona", persona)
    app.router.add_post("/api/budget", budget)
    app.router.add_post("/api/preset", preset)
    app.router.add_post("/api/mode", mode)
    app.router.add_post("/api/focus", focus)
    app.router.add_post("/api/reset", reset)
    app.router.add_get("/api/queue", queue_get)
    app.router.add_post("/api/queue/confirm", queue_confirm)
    app.router.add_get("/api/vitrine", vitrine)
    return app


def render_vitrine_text(state: RuntimeState) -> str:
    """Human-readable vitrine card (mirror of ha-state.txt format)."""
    stamp = "🟢 fresh" if state.vitrine.fresh else "🔴 STALE"
    age = state.vitrine.age_s
    age_txt = "no events yet" if age == float("inf") else f"{int(age)}s"
    header = f"{stamp} | snapshot age: {age_txt}"
    return "\n".join([header, "", *state.vitrine.lines]) + "\n"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
