"""Cycle A: chat session memory (chatsessions) + persona in the chat prompt."""

from __future__ import annotations

import json

import aiohttp
from aiohttp import web
from pilot_addon.chat import chat_ask
from pilot_addon.chatsessions import MAX_TURNS, ChatSessions
from pilot_addon.main import build_state

from .test_addon_chat import _start

# -- ChatSessions (pure) ---------------------------------------------------------


def test_sessions_append_history_and_cap(tmp_path):
    sessions = ChatSessions(tmp_path)
    assert sessions.history("conv-1") == []
    for i in range(MAX_TURNS + 5):
        sessions.append("conv-1", "user", f"вопрос {i}")
        sessions.append("conv-1", "assistant", f"ответ {i}")
    history = sessions.history("conv-1")
    assert len(history) == MAX_TURNS
    assert history[0]["text"] == "вопрос 10"  # oldest turns dropped
    assert history[-1]["text"] == f"ответ {MAX_TURNS + 4}"


def test_sessions_persist_across_instances(tmp_path):
    ChatSessions(tmp_path).append("conv-1", "user", "выключи свет в холле")
    reborn = ChatSessions(tmp_path)
    assert reborn.history("conv-1")[0]["text"] == "выключи свет в холле"


def test_sessions_are_isolated_and_slugged(tmp_path):
    sessions = ChatSessions(tmp_path)
    sessions.append("conv a/1", "user", "один")
    sessions.append("conv a/2", "user", "два")
    assert sessions.history("conv a/1")[0]["text"] == "один"
    assert sessions.history("conv a/2")[0]["text"] == "два"
    # Broken files degrade to empty history.
    for path in (tmp_path / "chats").glob("*.json"):
        path.write_text("garbage", encoding="utf-8")
    assert sessions.history("conv a/1") == []


def test_sessions_skip_blank_and_bad_roles(tmp_path):
    sessions = ChatSessions(tmp_path)
    sessions.append("c", "user", "   ")
    sessions.append("c", "narrator", "не роль")
    assert sessions.history("c") == []


def test_sessions_default_state_attached(tmp_path):
    state = build_state(tmp_path)
    state.sessions.append("c", "user", "привет")
    assert build_state(tmp_path).sessions.history("c")[0]["text"] == "привет"


# -- chat turn: history + persona --------------------------------------------------


async def test_chat_turn_uses_history_and_persona(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = '{"say": "Готово.", "actions": []}'
        state.apply_preset("observer")
        state.set_mode("vacation")
        state.set_persona("verbosity", 10)
        state.current_focus = "экономия"

        await chat_ask(state, "выключи свет в холле", conversation_id="c-1")
        # Second turn: the first exchange must be in the prompt.
        await chat_ask(state, "а в спальне тот же", conversation_id="c-1")

        _first, second = llm.requests
        system = second["messages"][0]["content"]
        user = second["messages"][1]["content"]
        # Persona block in the system prompt.
        assert "Тихий наблюдатель" in system
        assert "отпуск (дом пустует)" in system
        assert "предельно коротко" in system
        assert "экономия" in system
        # History block in the user message.
        assert "Недавний диалог:" in user
        assert "Хозяин: выключи свет в холле" in user
        assert "Пилот: Готово." in user
        # And the new message last.
        assert user.rstrip().endswith("Сообщение хозяина: а в спальне тот же")

        # Turns persisted on disk.
        disk = json.loads((tmp_path / "chats" / "c-1.json").read_text(encoding="utf-8"))
        assert [t["text"] for t in disk] == [
            "выключи свет в холле",
            "Готово.",
            "а в спальне тот же",
            "Готово.",
        ]
    finally:
        await close()


async def test_chat_turn_without_conversation_id_uses_default(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = '{"say": "Да.", "actions": []}'
        await chat_ask(state, "привет")
        await chat_ask(state, "что ты сказал?")
        assert "Хозяин: привет" in llm.requests[1]["messages"][1]["content"]
        assert (tmp_path / "chats" / "default.json").exists()
    finally:
        await close()


async def test_chat_api_passes_conversation_id(tmp_path, socket_enabled, monkeypatch):
    from pilot_addon.http_api import create_app

    state, _llm_port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    llm.content = '{"say": "Ок.", "actions": []}'
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"http://127.0.0.1:{port}/api/chat",
                json={"message": "привет", "conversation_id": "assist-42"},
            ) as resp:
                assert resp.status == 200
        assert state.sessions.history("assist-42")[0]["text"] == "привет"
    finally:
        await runner.cleanup()
        await close()
