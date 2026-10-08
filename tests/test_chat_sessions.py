"""Chat session storage unit tests.

Note: /api/chat no longer writes these sessions — the dialogue lives in the
bundled OpenClaw core (its own session store). ChatSessions stays until the
stage-7 cleanup removes the retired chat.py path.
"""

from __future__ import annotations

from pilot_addon.chatsessions import MAX_TURNS, ChatSessions
from pilot_addon.main import build_state

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
