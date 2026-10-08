"""Tests for the chat remote control: safety, /api/chat, /api/queue POST."""

from __future__ import annotations

from aiohttp import web
from pilot_addon import supervisor
from pilot_addon.http_api import create_app
from pilot_addon.main import build_state
from pilot_addon.safety import classify

# -- safety classification (pure) ------------------------------------------------


def test_safety_direct_whitelist():
    assert (
        classify({"domain": "light", "service": "turn_off", "entity_id": "light.hall"})
        == "direct"
    )
    assert (
        classify(
            {
                "domain": "media_player",
                "service": "media_pause",
                "entity_id": "media_player.tv",
            }
        )
        == "direct"
    )
    assert (
        classify(
            {
                "domain": "climate",
                "service": "set_temperature",
                "entity_id": "climate.living",
            }
        )
        == "direct"
    )
    assert (
        classify(
            {
                "domain": "input_number",
                "service": "set_value",
                "entity_id": "input_number.comfort",
            }
        )
        == "direct"
    )


def test_safety_queue_for_risky_and_unknown():
    # Switch is not whitelisted (relays/gates live there) — always a proposal.
    assert (
        classify({"domain": "switch", "service": "turn_on", "entity_id": "switch.pump"})
        == "queue"
    )
    # Gate/door-marked entities never execute directly, even on whitelisted domains.
    assert (
        classify(
            {"domain": "light", "service": "turn_on", "entity_id": "light.gate_lamp"}
        )
        == "queue"
    )
    # Unknown domain/service.
    assert (
        classify({"domain": "vacuum", "service": "start", "entity_id": "vacuum.robot"})
        == "queue"
    )
    assert (
        classify({"domain": "light", "service": "wave", "entity_id": "light.hall"})
        == "queue"
    )


def test_safety_refuse_surveillance_and_locks():
    assert (
        classify(
            {"domain": "camera", "service": "turn_on", "entity_id": "camera.porch"}
        )
        == "refuse"
    )
    assert (
        classify(
            {
                "domain": "alarm_control_panel",
                "service": "alarm_disarm",
                "entity_id": "alarm_control_panel.home",
            }
        )
        == "refuse"
    )
    assert (
        classify({"domain": "lock", "service": "unlock", "entity_id": "lock.door"})
        == "refuse"
    )


# -- /api/chat over a fake LLM ----------------------------------------------------


class FakeLlm:
    def __init__(self) -> None:
        self.content = '{"say": "Готово.", "actions": []}'
        self.requests: list[dict] = []

    async def handler(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        return web.json_response(
            {
                "choices": [{"message": {"content": self.content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )


async def _start(tmp_path, socket_enabled, monkeypatch):
    """Runtime + fake LLM provider; returns (state, client, llm, base_cfg)."""
    state = build_state(tmp_path)
    llm = FakeLlm()

    app = web.Application()
    app.router.add_post("/v1/chat/completions", llm.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(supervisor, "HA_API_BASE", f"http://127.0.0.1:{port}/api")
    state.write_config_section(
        "supervisor",
        {
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "chat-key",
            "model": "chat-model",
            "price_input_per_1m": 10.0,
            "price_output_per_1m": 20.0,
        },
    )
    client_app = create_app(state)
    client_runner = web.AppRunner(client_app)
    await client_runner.setup()
    client_site = web.TCPSite(client_runner, "127.0.0.1", 0)
    await client_site.start()
    client_port = client_site._server.sockets[0].getsockname()[1]

    async def close() -> None:
        await client_runner.cleanup()
        await runner.cleanup()

    return state, client_port, llm, close


async def test_queue_propose_endpoint(tmp_path, socket_enabled):
    """POST /api/queue enqueues a trust proposal (the chat path for risky asks)."""
    from pilot_addon.http_api import create_app

    state = build_state(tmp_path)
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        import aiohttp

        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"http://127.0.0.1:{port}/api/queue",
                json={
                    "title": "Чат: switch.turn_on → switch.pump",
                    "summary": "Запрошено из чата Assist",
                    "action": {
                        "type": "chat",
                        "kind": "service_call",
                        "domain": "switch",
                        "service": "turn_on",
                        "entity_id": "switch.pump",
                        "data": {},
                    },
                },
            ) as resp:
                data = await resp.json()
        assert data["ok"] is True
        assert len(state.queue.items) == 1
        assert state.queue.items[0].action["entity_id"] == "switch.pump"
        # Dedup: the same chat action lands on the existing item.
        import aiohttp as aio

        async with aio.ClientSession() as http:
            async with http.post(
                f"http://127.0.0.1:{port}/api/queue",
                json={
                    "title": "Чат: switch.turn_on → switch.pump",
                    "action": state.queue.items[0].action,
                },
            ) as resp:
                data = await resp.json()
        assert len(state.queue.items) == 1
    finally:
        await runner.cleanup()
