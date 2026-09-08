# -*- coding: utf-8 -*-
"""Continuous reader for one persistent Claude Code SDK client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import (
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    UserMessage,
)

logger = logging.getLogger(__name__)

_END = object()


class SessionReader:
    """Drain one SDK client for its lifetime and hand each turn its messages.

    The SDK buffers at most 100 unread messages and routes permission and hook
    requests through the same reader, so a client nobody reads between turns
    stops answering them: a background agent stalls once its output fills the
    buffer. This reader consumes every message and gives a QwenPaw turn only
    the messages of the CLI turn its prompt started.

    Every CLI turn (a prompt, a slash command, or a background agent waking
    the session) opens with a ``system``/``init`` frame and closes with a
    result. A prompt sent while a turn is under way either joins it, which
    the CLI shows by echoing the prompt inside that turn
    (``replay-user-messages``), or runs as the next turn once that result
    arrives. Messages outside a QwenPaw turn are dropped.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._turn: asyncio.Queue[Any] | None = None
        self._awaiting_prompt = False
        self._cli_turn_open = False
        self._task = asyncio.create_task(self._read())

    @property
    def closed(self) -> bool:
        """Whether the client's message stream has ended."""
        return self._task.done()

    def start_turn(self) -> AsyncIterator[Any]:
        """Claim the next CLI turn; call right before sending its prompt."""
        turn: asyncio.Queue[Any] = asyncio.Queue()
        if self.closed:
            turn.put_nowait(_END)
        else:
            self._awaiting_prompt = self._cli_turn_open
            self._turn = turn
        return self._turn_messages(turn)

    async def close(self) -> None:
        """Stop reading; the client itself is disconnected by its owner."""
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _turn_messages(
        self,
        turn: asyncio.Queue[Any],
    ) -> AsyncIterator[Any]:
        try:
            while True:
                item = await turn.get()
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
                if isinstance(item, ResultMessage):
                    return
        finally:
            if self._turn is turn:
                self._turn = None

    async def _read(self) -> None:
        error: Exception | None = None
        try:
            async for message in self._client.receive_messages():
                self._route(message)
        except Exception as exc:  # pylint: disable=broad-except
            error = exc
        finally:
            if self._turn is not None:
                self._turn.put_nowait(_END if error is None else error)
            elif error is not None:
                logger.warning(
                    "Claude Code message stream failed between turns",
                    exc_info=error,
                )

    def _route(self, message: Any) -> None:
        is_result = isinstance(message, ResultMessage)
        if isinstance(message, SystemMessage) and message.subtype == "init":
            self._cli_turn_open = True
        elif is_result:
            self._cli_turn_open = False
        turn = self._turn
        if turn is None:
            return
        if self._awaiting_prompt:
            if is_result:
                # The turn under way ended first; the prompt runs next.
                self._awaiting_prompt = False
                return
            if not _is_prompt_echo(message):
                return
            # The CLI folded the prompt into the turn under way.
            self._awaiting_prompt = False
        turn.put_nowait(message)
        if is_result:
            self._turn = None


def _is_prompt_echo(message: Any) -> bool:
    """Whether a message is the CLI replaying a prompt QwenPaw sent."""
    if not isinstance(message, UserMessage) or message.parent_tool_use_id:
        return False
    if (message.origin or {}).get("kind") not in (None, "human"):
        return False
    content = message.content
    return not (
        isinstance(content, list)
        and any(isinstance(block, ToolResultBlock) for block in content)
    )
