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
    POST /api/queue         {title, summary?, action} — enqueue a proposal
    POST /api/queue/confirm {id, decision}
    POST /api/chat          {message} — one Assist turn (LLM, budget-guarded):
                            {"say", "actions": [{mode: direct|queue|refuse, …}]}
    GET  /api/vitrine
    POST /api/vitrine/update
    GET  /api/entity-state?entity_id=…       — fresh reading from HA (agent tool)
    GET  /api/entity-history?entity_id=…&hours=24 — change history (agent tool)
    GET  /api/entity-statistics?entity_id=…&hours=24&period=hour — recorder stats
    WS   /ws               workshop SPA protocol (see ws_api.py)
    GET  /{anything}       workshop SPA static files (see workshop_static.py)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from aiohttp import web

from . import workshop_static, ws_api
from .logbuffer import LogBuffer, RingBufferHandler
from .state import RuntimeState

_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_HISTORY_HOURS_MAX = 168.0


def _auth_ok(request: web.Request, state: RuntimeState) -> bool:
    """Bearer token check; open when no token configured (trusted local net)."""
    if not state.token:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {state.token}"


def create_app(
    state: RuntimeState, workshop_dir: Path | None = None, executor: Any = None
) -> web.Application:
    """Build the aiohttp application serving the contract.

    workshop_dir overrides the workshop SPA dist location (tests, local
    dev); the default is the workshop_dist dir baked into the image.
    executor overrides the HA executor (tests); the core's tool plugin is
    the default caller of /api/action.
    """
    app = web.Application()
    app["state"] = state
    if executor is None:
        from .executor import HaExecutor

        executor = HaExecutor()
    app["executor"] = executor

    log_buffer = LogBuffer()
    app["log_buffer"] = log_buffer
    log_handler = RingBufferHandler(log_buffer)
    root_logger = logging.getLogger()

    async def _start_log_sink(app: web.Application) -> None:
        root_logger.addHandler(log_handler)

    async def _stop_log_sink(app: web.Application) -> None:
        root_logger.removeHandler(log_handler)
        for ws in list(app["ws_connections"]):
            await ws.close()

    app.on_startup.append(_start_log_sink)
    app.on_cleanup.append(_stop_log_sink)
    ws_api.attach_ws(app, state, log_buffer)

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
            state.queue.reset_stats()
        elif target == "all":
            state.current_focus = ""
            state.reset_cost()
            state.flags.clear()
            state.queue.clear()
            state.queue.reset_stats()
        else:
            return web.json_response({"error": "unknown target"}, status=400)
        state.queue._audit.record("reset", {"target": target})
        return web.json_response({"ok": True})

    async def queue_get(request: web.Request) -> web.Response:
        return web.json_response(
            {"items": [item.as_dict() for item in state.queue.items]}
        )

    async def queue_propose(request: web.Request) -> web.Response:
        """Enqueue a trust proposal (chat remote control, integrations)."""
        body = await request.json()
        title = str(body.get("title") or "").strip()
        action = body.get("action")
        if not title or not isinstance(action, dict):
            return web.json_response({"error": "title and action required"}, status=400)
        item_id = state.queue.propose(
            title=title,
            summary=str(body.get("summary") or ""),
            action=action,
        )
        return web.json_response({"ok": True, "id": item_id})

    async def queue_confirm(request: web.Request) -> web.Response:
        body = await request.json()
        ok = state.queue.confirm(str(body["id"]), str(body["decision"]))
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"ok": True})

    async def chat(request: web.Request) -> web.Response:
        """One conversation turn — through the bundled OpenClaw core agent.

        The core holds the dialogue, memory and sessions; its pilot-home
        tools reach the home only via /api/action (trust contour). The
        response contract for the HA integration stays {say, actions}.
        """
        from .corebridge import ask_core  # lazy: keeps startup import small

        body = await request.json()
        message = str(body.get("message") or "").strip()
        if not message:
            return web.json_response({"error": "message required"}, status=400)
        conversation_id = str(body.get("conversation_id") or "").strip() or None
        language = str(body.get("language") or "").strip() or None
        result = await ask_core(
            state, message, conversation_id=conversation_id, language=language
        )
        if result["ok"]:
            return web.json_response(
                {"say": result["reply"], "actions": [], "via": "core"}
            )
        if result["error"] == "budget":
            say = "Дневной лимит бюджета исчерпан — продолжим завтра."
        elif result["error"] == "not_configured":
            say = "Агент ещё не настроен — пройдите онбординг в мастерской."
        else:
            say = "Агент временно недоступен (ядро не отвечает), попробуйте позже."
        return web.json_response(
            {"say": say, "actions": [], "error": result["error"], "via": "core"}
        )

    async def action(request: web.Request) -> web.Response:
        """The core tool plugin's door into the home (trust-guarded).

        safety.classify decides: direct -> execute now, queue -> trust
        queue for the owner, refuse -> never. Never raises.
        """
        from .safety import classify

        state: RuntimeState = request.app["state"]
        executor = request.app["executor"]
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"status": "refused", "detail": "bad json"}, status=400
            )
        domain = str(body.get("domain") or "").strip()
        service = str(body.get("service") or "").strip()
        if not domain or not service:
            return web.json_response(
                {"status": "refused", "detail": "domain and service required"},
                status=400,
            )
        action_payload: dict[str, Any] = {
            "type": "service_call",
            "domain": domain,
            "service": service,
        }
        if body.get("entity_id"):
            action_payload["entity_id"] = str(body["entity_id"])
        if body.get("value") is not None:
            action_payload["value"] = body["value"]

        verdict = classify(action_payload)
        if verdict == "refuse":
            state.queue._audit.record("core.action.refused", {"action": action_payload})
            return web.json_response(
                {
                    "status": "refused",
                    "detail": "policy: this action is never executed",
                }
            )
        if verdict == "direct":
            try:
                result = await executor.apply(action_payload)
            except Exception as err:  # HA down — offer a retry via the queue
                target = action_payload.get("entity_id") or domain
                state.queue.propose(
                    title=f"⚠️ Не исполнено: {target}",
                    summary=f"Ошибка HA: {err}. Подтвердите повторно.",
                    action={**action_payload, "retry": True},
                )
                return web.json_response(
                    {"status": "queued", "detail": "ha error, re-queued"}
                )
            state.queue._audit.record(
                f"core.action.{result.status}", {"action": action_payload}
            )
            return web.json_response(
                {"status": result.status, "detail": str(result.detail)[:500]}
            )
        summary_target = action_payload.get("entity_id") or "дома"
        item_id = state.queue.propose(
            title=f"Действие агента: {domain}.{service}",
            summary=f"Запрошено агентом для {summary_target}",
            action=action_payload,
        )
        return web.json_response(
            {"status": "queued", "detail": f"queued for owner confirmation ({item_id})"}
        )

    # -- agent read tools: fresh state / history / statistics -------------

    def _entity_id(request: web.Request) -> str | None:
        eid = str(request.query.get("entity_id") or "")
        return eid if _ENTITY_ID_RE.match(eid) else None

    async def entity_state(request: web.Request) -> web.Response:
        """Fresh reading of one entity, straight from HA (read-only)."""
        from .homequery import ha_get

        eid = _entity_id(request)
        if eid is None:
            return web.json_response({"error": "bad entity_id"}, status=400)
        status, data = await ha_get(f"/api/states/{eid}")
        state.queue._audit.record(
            "core.query", {"tool": "home_state", "entity_id": eid, "ha_status": status}
        )
        if status == 404:
            return web.json_response({"error": "entity not found"}, status=404)
        if status != 200 or not isinstance(data, dict):
            return web.json_response({"error": f"ha http {status}"}, status=502)
        return web.json_response(
            {
                "entity_id": eid,
                "state": data.get("state"),
                "attrs": data.get("attributes"),
                "last_changed": data.get("last_changed"),
                "last_updated": data.get("last_updated"),
            }
        )

    async def entity_find(request: web.Request) -> web.Response:
        """Find entities by name/entity_id substring (vitrine, read-only)."""
        query = str(request.query.get("q") or "").strip().lower()
        if len(query) < 2:
            return web.json_response({"error": "q too short"}, status=400)
        matches = []
        for eid, sample in sorted(state.vitrine.states.items()):
            if not isinstance(sample, dict):
                continue
            name = str(sample.get("attrs", {}).get("friendly_name") or eid)
            if query in eid.lower() or query in name.lower():
                matches.append(
                    {
                        "entity_id": eid,
                        "name": name,
                        "state": sample.get("state"),
                        "area": sample.get("area"),
                    }
                )
            if len(matches) >= 10:
                break
        return web.json_response({"q": query, "matches": matches})

    async def entity_history(request: web.Request) -> web.Response:
        """Change history: turn-on times, trends (read-only)."""
        from datetime import UTC, datetime, timedelta

        from .homequery import compact_history, ha_get

        eid = _entity_id(request)
        if eid is None:
            return web.json_response({"error": "bad entity_id"}, status=400)
        try:
            hours = min(
                _HISTORY_HOURS_MAX, max(1.0, float(request.query.get("hours", "24")))
            )
        except ValueError:
            hours = 24.0
        start = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        from urllib.parse import quote

        # The Supervisor proxy rejects some path-encodings of the ISO
        # timestamp (404 where a direct call returns 200) — walk formats
        # until one passes.
        naive = start.split("+")[0].replace("T", " ")
        variants = [quote(start, safe=""), start, naive, quote(naive, safe="")]
        status, raw = 0, None
        for variant in variants:
            status, raw = await ha_get(
                f"/api/history/period/{variant}",
                params={"filter_entity_id": eid, "minimal_response": ""},
            )
            if status == 200:
                break
        state.queue._audit.record(
            "core.query",
            {
                "tool": "home_history",
                "entity_id": eid,
                "hours": hours,
                "ha_status": status,
            },
        )
        if status != 200:
            logging.getLogger("pilot.addon").warning(
                "core.query failed: tool=home_history entity=%s ha_status=%s",
                eid,
                status,
            )
            return web.json_response({"error": f"ha http {status}"}, status=502)
        return web.json_response({"entity_id": eid, **compact_history(raw, hours)})

    async def entity_statistics(request: web.Request) -> web.Response:
        """Recorder statistics: energy sums, averages (read-only)."""
        from datetime import UTC, datetime, timedelta

        from .homequery import ha_post

        eid = _entity_id(request)
        if eid is None:
            return web.json_response({"error": "bad entity_id"}, status=400)
        try:
            hours = min(
                _HISTORY_HOURS_MAX, max(1.0, float(request.query.get("hours", "24")))
            )
        except ValueError:
            hours = 24.0
        period = str(request.query.get("period") or "hour")
        if period not in ("hour", "day", "5minute"):
            period = "hour"
        start = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        status, raw = await ha_post(
            "/api/recorder/statistics_during_period",
            {
                "start_time": start,
                "statistic_ids": [eid],
                "period": period,
                "types": ["state", "sum", "mean", "min", "max"],
            },
        )
        state.queue._audit.record(
            "core.query",
            {
                "tool": "home_statistics",
                "entity_id": eid,
                "hours": hours,
                "ha_status": status,
            },
        )
        if status != 200 or not isinstance(raw, list) or not raw:
            return web.json_response(
                {"entity_id": eid, "error": f"no statistics (ha http {status})"},
                status=404 if status == 400 else 502,
            )
        block = raw[0] if isinstance(raw[0], dict) else {}
        starts = block.get("start") or []
        points = []
        for idx, ts in enumerate(starts):
            point: dict[str, Any] = {"start": ts}
            for key in ("state", "sum", "mean", "min", "max"):
                values = block.get(key)
                if isinstance(values, list) and idx < len(values):
                    point[key] = values[idx]
            points.append(point)
        return web.json_response(
            {"entity_id": eid, "period": period, "hours": hours, "points": points}
        )

    async def core_run(request: web.Request) -> web.Response:
        """Receive a finished core automation run (evening round webhook).

        Non-empty results land in the trust queue as an informational note;
        every run is audited. The exact webhook payload shape is core-owned,
        so text extraction is defensive across field names.
        """
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad json"}, status=400)
        run_status = str(body.get("status") or "ok")
        text = ""
        for key in ("summary", "result", "text", "message", "output", "content"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        state.queue._audit.record(
            "core.run",
            {
                "name": str(body.get("name") or body.get("jobName") or "core-run"),
                "status": run_status,
                "has_summary": bool(text),
                "estimated": True,
            },
        )
        if text and run_status == "ok":
            state.queue.propose(
                title="Вечерний обход Пилота",
                summary=text[:300],
                action={"kind": "proposal", "title": text[:300]},
            )
        return web.json_response({"ok": True})

    async def vitrine(request: web.Request) -> web.Response:
        return web.json_response(state.vitrine.as_dict())

    async def vitrine_update(request: web.Request) -> web.Response:
        """Receive a state push from the HA integration (token-free, local)."""
        body = await request.json()
        states = body.get("states")
        if not isinstance(states, dict):
            return web.json_response({"error": "states required"}, status=400)
        state.push_vitrine(states)
        return web.json_response({"ok": True, "entities": len(states)})

    app.router.add_get("/api/validate", validate)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/persona", persona)
    app.router.add_post("/api/budget", budget)
    app.router.add_post("/api/preset", preset)
    app.router.add_post("/api/mode", mode)
    app.router.add_post("/api/focus", focus)
    app.router.add_post("/api/reset", reset)
    app.router.add_get("/api/queue", queue_get)
    app.router.add_post("/api/queue", queue_propose)
    app.router.add_post("/api/queue/confirm", queue_confirm)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/action", action)
    app.router.add_post("/api/core-run", core_run)
    app.router.add_get("/api/entity-state", entity_state)
    app.router.add_get("/api/entity-find", entity_find)
    app.router.add_get("/api/entity-history", entity_history)
    app.router.add_get("/api/entity-statistics", entity_statistics)
    app.router.add_get("/api/vitrine", vitrine)
    app.router.add_post("/api/vitrine/update", vitrine_update)
    # Catch-all last: the SPA route matches every GET, so the contract
    # routes above must already be registered to keep winning.
    workshop_static.attach_workshop(app, workshop_dir)
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
