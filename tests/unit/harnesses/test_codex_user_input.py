# -*- coding: utf-8 -*-
"""Tests for pending Codex request-user-input lifecycle."""

from __future__ import annotations

import asyncio

import pytest

from qwenpaw.harnesses.codex.user_input import (
    CodexUserInputBroker,
    PendingUserInputNotFoundError,
    PendingUserInputSessionMismatchError,
    PendingUserInputTimedOutError,
)


@pytest.mark.asyncio
async def test_resolves_pending_answers_for_matching_session() -> None:
    broker = CodexUserInputBroker()
    pending = broker.register(
        session_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
        questions=[{"id": "choice", "question": "Pick one"}],
    )
    answers = {"choice": {"answers": ["First"]}}

    broker.resolve("chat-1", pending.request_id, answers)

    assert await broker.wait(pending) == answers
    assert broker.get(pending.request_id) is None


@pytest.mark.asyncio
async def test_rejects_answer_from_a_different_session() -> None:
    broker = CodexUserInputBroker()
    pending = broker.register(
        session_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
        questions=[],
    )

    with pytest.raises(PendingUserInputSessionMismatchError):
        broker.resolve("chat-2", pending.request_id, {})

    broker.cancel_all()
    with pytest.raises(asyncio.CancelledError):
        await broker.wait(pending)


def test_rejects_unknown_request() -> None:
    broker = CodexUserInputBroker()

    with pytest.raises(PendingUserInputNotFoundError):
        broker.resolve("chat-1", "missing", {})


@pytest.mark.asyncio
async def test_pending_request_expires() -> None:
    broker = CodexUserInputBroker(timeout_seconds=0.01)
    pending = broker.register(
        session_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
        questions=[],
    )

    with pytest.raises(PendingUserInputTimedOutError):
        await broker.wait(pending)

    assert broker.get(pending.request_id) is None


@pytest.mark.asyncio
async def test_cancels_only_requests_for_the_finished_turn() -> None:
    broker = CodexUserInputBroker()
    first = broker.register(
        session_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
        questions=[],
    )
    second = broker.register(
        session_id="chat-2",
        thread_id="thread-2",
        turn_id="turn-2",
        item_id="item-2",
        questions=[],
    )

    broker.cancel_turn("thread-1", "turn-1")

    with pytest.raises(asyncio.CancelledError):
        await broker.wait(first)
    assert broker.get(second.request_id) is second
    broker.cancel_all()
