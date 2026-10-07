"""Cycle B: the owner's decision history feeds the chat context."""

from __future__ import annotations

from pilot_addon.chat import chat_ask

from .test_addon_chat import _start


async def test_owner_memory_block_appears_in_prompt(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = '{"say": "Понял.", "actions": []}'
        # A decision history: one accepted, two rejected.
        first = state.queue.propose(
            "Уставка 22 в спальне", {"type": "x", "entity_id": "number.bedroom"}
        )
        state.queue.confirm(first, "yes")
        for title in ("Открыть калитку в 7 утра", "Поднять уставку котла до 80"):
            item = state.queue.propose(title, {"type": "x", "flag": title})
            state.queue.confirm(item, "no")

        await chat_ask(state, "предложи что-нибудь", conversation_id="c-1")
        user = llm.requests[0]["messages"][1]["content"]
        assert "История решений хозяина:" in user
        assert "принято 1" in user and "отклонено 2" in user
        assert "number: принято 1/отклонено 0" in user
        assert "- Открыть калитку в 7 утра" in user
        assert "- Поднять уставку котла до 80" in user

        # The block is part of the user message, before the current message.
        assert user.index("История решений хозяина:") < user.index("Сообщение хозяина:")
    finally:
        await close()


async def test_owner_memory_absent_with_clean_history(
    tmp_path, socket_enabled, monkeypatch
):
    state, _port, llm, close = await _start(tmp_path, socket_enabled, monkeypatch)
    try:
        llm.content = '{"say": "Привет.", "actions": []}'
        await chat_ask(state, "привет")
        user = llm.requests[0]["messages"][1]["content"]
        # Totals line is always there (all zeros), rejections list is not.
        assert "История решений хозяина:" in user
        assert "Недавние отказы:" not in user
    finally:
        await close()
