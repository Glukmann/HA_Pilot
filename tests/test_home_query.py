"""Agent read tools: fresh entity state, history, recorder statistics."""

from __future__ import annotations

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from pilot_addon.homequery import compact_history
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state


async def _fake_ha(socket_enabled, monkeypatch, routes: dict):
    """A fake HA Core API; HA_API_BASE is repointed at it."""

    async def handler(request: web.Request) -> web.Response:
        key = request.path
        if key in routes:
            body = routes[key](request)
            if isinstance(body, tuple):
                return web.json_response(body[1], status=body[0])
            return web.json_response(body)
        return web.json_response({"message": "not found"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv("HA_API_BASE", str(server.make_url("")))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    return server


async def _start_addon(tmp_path):
    state = build_state(tmp_path)
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return site._server.sockets[0].getsockname()[1], runner


async def _get(port, path):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}{path}") as resp:
            return resp.status, await resp.json()


async def test_entity_state_proxies_ha(tmp_path, socket_enabled, monkeypatch):
    server = await _fake_ha(
        socket_enabled,
        monkeypatch,
        {
            "/api/states/sensor.living_temp": lambda r: {
                "state": "26.3",
                "attributes": {"unit_of_measurement": "°C"},
                "last_changed": "2026-10-08T13:00:00+00:00",
                "last_updated": "2026-10-08T13:00:00+00:00",
            }
        },
    )
    port, runner = await _start_addon(tmp_path)
    try:
        status, body = await _get(
            port, "/api/entity-state?entity_id=sensor.living_temp"
        )
        assert status == 200
        assert body["state"] == "26.3"
        assert body["attrs"]["unit_of_measurement"] == "°C"
        # Bad ids never reach HA.
        status, _ = await _get(port, "/api/entity-state?entity_id=../../etc")
        assert status == 400
    finally:
        await runner.cleanup()
        await server.close()


async def test_entity_history_compact(tmp_path, socket_enabled, monkeypatch):
    async def hist_any(request: web.Request) -> web.Response:
        if request.path.startswith("/api/history/period/"):
            assert "filter_entity_id" in request.query
            return web.json_response(
                [
                    [
                        {
                            "entity_id": "sensor.t",
                            "state": "24.0",
                            "last_changed": "2026-10-08T10:00:00+00:00",
                        },
                        {
                            "entity_id": "sensor.t",
                            "state": "25.0",
                            "last_changed": "2026-10-08T11:00:00+00:00",
                        },
                        {
                            "entity_id": "sensor.t",
                            "state": "26.0",
                            "last_changed": "2026-10-08T12:00:00+00:00",
                        },
                    ]
                ]
            )
        return web.json_response({"message": "nf"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", hist_any)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv("HA_API_BASE", str(server.make_url("")))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")

    port, runner = await _start_addon(tmp_path)
    try:
        status, body = await _get(
            port, "/api/entity-history?entity_id=sensor.t&hours=6"
        )
        assert status == 200
        assert len(body["points"]) == 3
        assert body["summary"]["min"] == 24.0
        assert body["summary"]["max"] == 26.0
        assert body["summary"]["last"] == 26.0
    finally:
        await runner.cleanup()
        await server.close()


async def test_entity_statistics_compact(tmp_path, socket_enabled, monkeypatch):
    async def stats(request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["statistic_ids"] == ["sensor.energy"]
        return web.json_response(
            [
                {
                    "statistic_id": "sensor.energy",
                    "start": ["2026-10-08T10:00:00+00:00", "2026-10-08T11:00:00+00:00"],
                    "state": [10.0, 10.5],
                    "sum": [None, 0.5],
                    "mean": [None, None],
                    "min": [None, None],
                    "max": [None, None],
                }
            ]
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", stats)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv("HA_API_BASE", str(server.make_url("")))

    port, runner = await _start_addon(tmp_path)
    try:
        status, body = await _get(
            port, "/api/entity-statistics?entity_id=sensor.energy&hours=24"
        )
        assert status == 200
        assert len(body["points"]) == 2
        assert body["points"][1]["sum"] == 0.5
        assert body["points"][1]["state"] == 10.5
    finally:
        await runner.cleanup()
        await server.close()


def test_compact_history_numeric_guard():
    raw = [
        [
            {"state": "on", "last_changed": "t1"},
            {"state": "off", "last_changed": "t2"},
        ]
    ]
    out = compact_history(raw, 24)
    assert out["summary"] is None
    assert out["points"] == [["t1", "on"], ["t2", "off"]]
    assert compact_history([], 24)["points"] == []
