"""WebSocket API of the Pilot add-on — the channel the workshop SPA speaks.

Protocol: JSON text frames of the shape {"type": ..., "payload": ...}.

Commands (client -> server), each answered with a frame of the same type:
    status                          -> payload: runtime snapshot (state.snapshot())
    vitrine/get                     -> payload: {"fresh", "lines"}
    queue/get                       -> payload: {"items": [...]}
    queue/confirm   {id, decision}  -> payload: {"ok": true}; on failure an error
    logs/recent     {limit?}        -> payload: [entry, ...] (tail of the buffer)
    logs/subscribe                  -> ack, then a {type: "log"} event per record
    logs/unsubscribe                -> ack; log events stop
    persona/set     {slider, value} -> payload: {"ok": true} (a persona slider)
    preset/apply    {preset}        -> payload: {"ok": true} (persona preset)
    budget/set      {value}         -> payload: {"ok": true} (daily LLM budget)
    mode/set        {mode}          -> payload: {"ok": true} (home mode)
    config/get                      -> payload: {configured, sections} — the
                                       runtime config file with secret values
                                       masked ("***"); configured:false when
                                       the file is absent or not valid JSON
    config/set      {section, values} -> payload: {ok: true, section} — deep
                                       merge of values into that config file
                                       section (atomic write; a broken file is
                                       backed up as pilot.json.bad-<ts>)

Server -> client events:
    log    new record for log subscribers (after logs/subscribe)
    queue  broadcast to all connections after a successful queue/confirm
    status broadcast to all connections after a successful persona/set,
           preset/apply, budget/set, mode/set or config/set (fresh
           state.snapshot(), so clients refresh their cards without polling)

Failures (unknown command, bad JSON, bad payload, rejected confirm) answer
{type: "error", payload: {"message"}} and the connection stays open.

Auth: none in Phase 5 (plan non-goals); in the add-on deployment the endpoint
is reachable only through the HA-authenticated Supervisor ingress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

from aiohttp import WSMsgType, web

from .logbuffer import LogBuffer
from .state import RuntimeState

logger = logging.getLogger("pilot.addon")

Broadcast = Callable[[str, Any], Awaitable[None]]


class CommandError(Exception):
    """A command failed; the message is sent back as the error payload."""


def _frame(msg_type: str, payload: Any) -> str:
    return json.dumps({"type": msg_type, "payload": payload}, ensure_ascii=False)


async def _send(ws: web.WebSocketResponse, msg_type: str, payload: Any) -> None:
    await ws.send_str(_frame(msg_type, payload))


class WsSession:
    """Per-connection state: log subscription + event delivery scheduling."""

    def __init__(
        self, ws: web.WebSocketResponse, state: RuntimeState, log_buffer: LogBuffer
    ) -> None:
        self.ws = ws
        self.state = state
        self.log_buffer = log_buffer
        self._loop = asyncio.get_running_loop()
        self._log_callback: Callable[[dict[str, Any]], None] | None = None
        self._pending: set[asyncio.Task[None]] = set()

    def subscribe_logs(self) -> None:
        if self._log_callback is None:
            self._log_callback = self._on_log
            self.log_buffer.subscribe(self._log_callback)

    def unsubscribe_logs(self) -> None:
        if self._log_callback is not None:
            self.log_buffer.unsubscribe(self._log_callback)
            self._log_callback = None

    def _on_log(self, entry: dict[str, Any]) -> None:
        if self._log_callback is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._deliver_log, entry)
        except RuntimeError:
            self.unsubscribe_logs()  # loop already closed

    def _deliver_log(self, entry: dict[str, Any]) -> None:
        if self._log_callback is not None:
            task = asyncio.ensure_future(self._send_log(entry))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def _send_log(self, entry: dict[str, Any]) -> None:
        if self._log_callback is None:
            return
        try:
            await _send(self.ws, "log", entry)
        except (ConnectionResetError, RuntimeError):
            self.unsubscribe_logs()


CommandHandler = Callable[[WsSession, dict[str, Any]], Awaitable[Any]]

# Successful setters broadcast a fresh status snapshot to every connection,
# so all clients refresh their cards without polling.
_STATUS_BROADCAST_COMMANDS = frozenset(
    {"persona/set", "preset/apply", "budget/set", "mode/set", "config/set"}
)


def _queue_payload(state: RuntimeState) -> dict[str, Any]:
    return {"items": [item.as_dict() for item in state.queue.items]}


async def _cmd_status(session: WsSession, payload: dict[str, Any]) -> dict[str, Any]:
    return session.state.snapshot()


async def _cmd_vitrine_get(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    return session.state.vitrine.as_dict()


async def _cmd_queue_get(session: WsSession, payload: dict[str, Any]) -> dict[str, Any]:
    return _queue_payload(session.state)


async def _cmd_queue_confirm(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        item_id = str(payload["id"])
        decision = str(payload["decision"])
    except KeyError as err:
        raise CommandError(f"missing field: {err.args[0]}") from err
    if not session.state.queue.confirm(item_id, decision):
        raise CommandError("not found")
    return {"ok": True}


async def _cmd_logs_recent(
    session: WsSession, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    limit = payload.get("limit")
    try:
        limit_n = None if limit is None else max(1, int(limit))
    except (TypeError, ValueError) as err:
        raise CommandError("limit must be an integer") from err
    return session.log_buffer.recent(limit_n)


async def _cmd_logs_subscribe(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    session.subscribe_logs()
    return {"ok": True}


async def _cmd_logs_unsubscribe(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    session.unsubscribe_logs()
    return {"ok": True}


async def _cmd_persona_set(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        slider = str(payload["slider"])
        value = int(payload["value"])
    except KeyError as err:
        raise CommandError(f"missing field: {err.args[0]}") from err
    except (TypeError, ValueError) as err:
        raise CommandError("value must be an integer") from err
    try:
        session.state.set_persona(slider, value)
    except ValueError as err:
        raise CommandError(str(err)) from err
    return {"ok": True}


async def _cmd_preset_apply(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        preset = str(payload["preset"])
    except KeyError as err:
        raise CommandError(f"missing field: {err.args[0]}") from err
    try:
        session.state.apply_preset(preset)
    except ValueError as err:
        raise CommandError(str(err)) from err
    return {"ok": True}


async def _cmd_budget_set(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        value = float(payload["value"])
    except KeyError as err:
        raise CommandError(f"missing field: {err.args[0]}") from err
    except (TypeError, ValueError) as err:
        raise CommandError("value must be a number") from err
    session.state.daily_budget = value
    return {"ok": True}


async def _cmd_mode_set(session: WsSession, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        mode = str(payload["mode"])
    except KeyError as err:
        raise CommandError(f"missing field: {err.args[0]}") from err
    try:
        session.state.set_mode(mode)
    except ValueError as err:
        raise CommandError(str(err)) from err
    return {"ok": True}


_SECRET_KEY_MARKERS = ("token", "secret", "password", "apikey", "api_key", "key")
_SECRET_MASK = "***"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _mask_secrets(node: Any) -> Any:
    """Deep-copy a config structure, masking values under secret-looking keys.

    Keys are never dropped — the UI shows that a setting exists, just not
    its value. A secret key masks its whole value (string or container).
    """
    if isinstance(node, dict):
        return {
            key: _SECRET_MASK if _is_secret_key(str(key)) else _mask_secrets(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_mask_secrets(item) for item in node]
    return node


async def _cmd_config_get(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    raw = session.state.read_config()
    if raw is None:
        return {"configured": False, "sections": {}}
    return {"configured": True, "sections": _mask_secrets(raw)}


async def _cmd_config_set(
    session: WsSession, payload: dict[str, Any]
) -> dict[str, Any]:
    section = payload.get("section")
    if not isinstance(section, str) or not section:
        raise CommandError("section must be a non-empty string")
    values = payload.get("values")
    if not isinstance(values, dict):
        raise CommandError("values must be an object")
    try:
        session.state.write_config_section(section, values)
    except (OSError, ValueError) as err:
        raise CommandError(f"config write failed: {err}") from err
    # Audit and logs never carry the values — they may contain secrets.
    session.state.queue._audit.record("config.set", {"section": section})
    logger.info("config.set section=%s", section)
    return {"ok": True, "section": section}


def _registry() -> dict[str, CommandHandler]:
    return {
        "status": _cmd_status,
        "vitrine/get": _cmd_vitrine_get,
        "queue/get": _cmd_queue_get,
        "queue/confirm": _cmd_queue_confirm,
        "logs/recent": _cmd_logs_recent,
        "logs/subscribe": _cmd_logs_subscribe,
        "logs/unsubscribe": _cmd_logs_unsubscribe,
        "persona/set": _cmd_persona_set,
        "preset/apply": _cmd_preset_apply,
        "budget/set": _cmd_budget_set,
        "mode/set": _cmd_mode_set,
        "config/get": _cmd_config_get,
        "config/set": _cmd_config_set,
    }


async def _dispatch(
    session: WsSession,
    handlers: dict[str, CommandHandler],
    raw: str,
    broadcast: Broadcast,
) -> None:
    ws = session.ws
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await _send(ws, "error", {"message": "invalid JSON"})
        return
    if not isinstance(data, dict):
        await _send(ws, "error", {"message": "message must be an object"})
        return
    cmd = data.get("type")
    if not isinstance(cmd, str):
        await _send(ws, "error", {"message": "type required"})
        return
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        await _send(ws, "error", {"message": "payload must be an object"})
        return
    handler = handlers.get(cmd)
    if handler is None:
        await _send(ws, "error", {"message": f"unknown command: {cmd}"})
        return
    try:
        result = await handler(session, payload)
    except CommandError as err:
        await _send(ws, "error", {"message": str(err)})
        return
    except Exception as err:  # a handler bug must not kill the socket
        await _send(ws, "error", {"message": f"{type(err).__name__}: {err}"})
        return
    await _send(ws, cmd, result)
    if cmd == "queue/confirm" and isinstance(result, dict) and result.get("ok"):
        await broadcast("queue", _queue_payload(session.state))
    elif cmd in _STATUS_BROADCAST_COMMANDS:
        await broadcast("status", session.state.snapshot())


def attach_ws(app: web.Application, state: RuntimeState, log_buffer: LogBuffer) -> None:
    """Register the /ws endpoint on the aiohttp application."""
    connections: set[web.WebSocketResponse] = set()
    app["ws_connections"] = connections
    handlers = _registry()

    async def broadcast(msg_type: str, payload: Any) -> None:
        for ws in list(connections):
            try:
                await ws.send_str(_frame(msg_type, payload))
            except (ConnectionResetError, RuntimeError):
                connections.discard(ws)

    # Lets non-WS producers (the daily supervisor run) push status events.
    app["ws_broadcast"] = broadcast

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connections.add(ws)
        session = WsSession(ws, state, log_buffer)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await _dispatch(session, handlers, msg.data, broadcast)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            session.unsubscribe_logs()
            connections.discard(ws)
        return ws

    app.router.add_get("/ws", ws_handler)
