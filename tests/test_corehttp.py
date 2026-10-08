"""Tests for the OpenClaw core health probe."""

from __future__ import annotations

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from pilot_addon.corehttp import CORE_BASE, core_health


async def _health_ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def test_health_alive(socket_enabled):
    app = web.Application()
    app.router.add_get("/healthz", _health_ok)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await core_health(session, base=str(server.make_url("")))
    finally:
        await server.close()
    assert result["alive"] is True
    assert result["detail"] == "ok"


async def test_health_down_never_raises(socket_enabled):
    async with aiohttp.ClientSession() as session:
        result = await core_health(session, base="http://127.0.0.1:1", timeout_s=0.2)
    assert result["alive"] is False
    assert result["detail"]
    assert CORE_BASE == "http://127.0.0.1:18789"


async def test_health_http_error_is_down(socket_enabled):
    app = web.Application()
    app.router.add_get("/healthz", lambda r: web.Response(status=503))
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await core_health(session, base=str(server.make_url("")))
    finally:
        await server.close()
    assert result["alive"] is False
    assert result["detail"] == "http 503"
