"""Tests for serving the workshop SPA static files from the add-on."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path

import aiohttp
from aiohttp import web
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state
from pilot_addon.state import RuntimeState
import pytest

INDEX_HTML = "<!doctype html><title>workshop</title>"
ASSET_JS = "console.log('workshop-asset');"


def _build_dist(base: Path, with_index: bool = True) -> Path:
    dist = base / "dist"
    (dist / "assets").mkdir(parents=True)
    if with_index:
        (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text(ASSET_JS, encoding="utf-8")
    return dist


@pytest.fixture
async def addon(tmp_path, socket_enabled) -> AsyncIterator[tuple[RuntimeState, int]]:
    """Add-on with a minimal workshop dist on an ephemeral port."""
    state = build_state(tmp_path)
    dist = _build_dist(tmp_path)
    app = create_app(state, workshop_dir=dist)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield state, port
    await runner.cleanup()


async def test_index_served_at_root(addon) -> None:
    """GET / returns index.html without long-term caching."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            assert await resp.text() == INDEX_HTML
            assert resp.headers["Cache-Control"] == "no-cache"


async def test_asset_served_with_cache(addon) -> None:
    """Hashed assets get immutable caching."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://127.0.0.1:{port}/assets/index-abc123.js"
        ) as resp:
            assert resp.status == 200
            assert await resp.text() == ASSET_JS
            assert "immutable" in resp.headers["Cache-Control"]


async def test_missing_asset_is_404_not_html(addon) -> None:
    """A missing file-looking path 404s instead of returning the SPA shell."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/assets/gone.js") as resp:
            assert resp.status == 404
            assert "text/html" not in resp.headers["Content-Type"]


async def test_extensionless_path_falls_back_to_index(addon) -> None:
    """Deep links without a file extension get the SPA shell."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/some/deep/route") as resp:
            assert resp.status == 200
            assert await resp.text() == INDEX_HTML


async def test_unknown_api_path_is_not_intercepted(addon) -> None:
    """Unknown /api/* paths 404 as JSON — the SPA fallback never masks them."""
    _state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/no-such-route") as resp:
            assert resp.status == 404
            body = await resp.json()
            assert body == {"error": "not found"}


async def test_ingress_prefix_is_honored(addon) -> None:
    """A prefix-keeping proxy is handled via X-Ingress-Path."""
    _state, port = addon
    prefix = "/api/hassio_ingress/test-token"
    headers = {"X-Ingress-Path": prefix}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://127.0.0.1:{port}{prefix}/", headers=headers
        ) as resp:
            assert resp.status == 200
            assert await resp.text() == INDEX_HTML
        async with session.get(
            f"http://127.0.0.1:{port}{prefix}/assets/index-abc123.js",
            headers=headers,
        ) as resp:
            assert resp.status == 200
            assert await resp.text() == ASSET_JS


async def test_contract_routes_win_over_spa_fallback(addon) -> None:
    """Regression: REST /api/* and the /ws upgrade keep working (panel safe)."""
    state, port = addon
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/status") as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body["runtime_version"] == state.runtime_version

        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            await ws.send_str(json.dumps({"type": "status", "payload": {}}))
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            reply = json.loads(msg.data)
            assert reply["type"] == "status"
            assert reply["payload"]["runtime_version"] == state.runtime_version


async def test_missing_dist_404s_cleanly(tmp_path, socket_enabled) -> None:
    """Without a built dist the UI 404s but the API contract still works."""
    state = build_state(tmp_path)
    empty_dist = _build_dist(tmp_path, with_index=False)
    app = create_app(state, workshop_dir=empty_dist)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 404
                body = await resp.json()
                assert body == {"error": "workshop UI not built"}
            async with session.get(f"http://127.0.0.1:{port}/api/status") as resp:
                assert resp.status == 200
    finally:
        await runner.cleanup()
