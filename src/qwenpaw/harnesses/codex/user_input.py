# -*- coding: utf-8 -*-
"""Lifecycle for Codex requests that wait for console user input."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_USER_INPUT_TIMEOUT_SECONDS = 24 * 60 * 60

CodexAnswers = dict[str, dict[str, list[str]]]


class PendingUserInputNotFoundError(LookupError):
    """Raised when a pending request no longer exists."""


class PendingUserInputSessionMismatchError(PermissionError):
    """Raised when a request is answered from another chat session."""


class PendingUserInputTimedOutError(TimeoutError):
    """Raised when Codex user input was not answered in time."""


@dataclass
class PendingCodexUserInput:
    """One Codex server request suspended until the user answers."""

    request_id: str
    session_id: str
    thread_id: str
    turn_id: str
    item_id: str
    questions: list[dict[str, Any]]
    expires_at: str
    deadline: float
    future: asyncio.Future[CodexAnswers]


class CodexUserInputBroker:
    """Own pending user-input futures for one workspace adapter."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_USER_INPUT_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._pending: dict[str, PendingCodexUserInput] = {}

    def register(
        self,
        *,
        session_id: str,
        thread_id: str,
        turn_id: str,
        item_id: str,
        questions: list[dict[str, Any]],
    ) -> PendingCodexUserInput:
        """Register a request before its UI event is published."""
        loop = asyncio.get_running_loop()
        request_id = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=self._timeout_seconds,
        )
        pending = PendingCodexUserInput(
            request_id=request_id,
            session_id=session_id,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            questions=[dict(question) for question in questions],
            expires_at=expires.isoformat(timespec="seconds"),
            deadline=loop.time() + self._timeout_seconds,
            future=loop.create_future(),
        )
        self._pending[request_id] = pending
        return pending

    def get(self, request_id: str) -> PendingCodexUserInput | None:
        """Return an active request without mutating it."""
        return self._pending.get(request_id)

    def resolve(
        self,
        session_id: str,
        request_id: str,
        answers: CodexAnswers,
    ) -> None:
        """Resolve an active request after validating session ownership."""
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            raise PendingUserInputNotFoundError(request_id)
        if pending.session_id != session_id:
            raise PendingUserInputSessionMismatchError(request_id)
        if asyncio.get_running_loop().time() >= pending.deadline:
            self._pending.pop(request_id, None)
            pending.future.cancel()
            raise PendingUserInputTimedOutError(request_id)
        pending.future.set_result(answers)

    async def wait(
        self,
        pending: PendingCodexUserInput,
    ) -> CodexAnswers:
        """Wait until an answer, cancellation, or the broker deadline."""
        timeout = max(
            0.0,
            pending.deadline - asyncio.get_running_loop().time(),
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.future),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            pending.future.cancel()
            raise PendingUserInputTimedOutError(
                pending.request_id,
            ) from exc
        finally:
            if self._pending.get(pending.request_id) is pending:
                self._pending.pop(pending.request_id, None)

    def cancel_turn(self, thread_id: str, turn_id: str) -> None:
        """Cancel pending questions owned by one finished turn."""
        for request_id, pending in tuple(self._pending.items()):
            if pending.thread_id != thread_id or pending.turn_id != turn_id:
                continue
            self._pending.pop(request_id, None)
            pending.future.cancel()

    def cancel_all(self) -> None:
        """Cancel every request when the adapter shuts down."""
        for request_id, pending in tuple(self._pending.items()):
            self._pending.pop(request_id, None)
            pending.future.cancel()


__all__ = [
    "CodexAnswers",
    "CodexUserInputBroker",
    "DEFAULT_USER_INPUT_TIMEOUT_SECONDS",
    "PendingCodexUserInput",
    "PendingUserInputNotFoundError",
    "PendingUserInputSessionMismatchError",
    "PendingUserInputTimedOutError",
]
