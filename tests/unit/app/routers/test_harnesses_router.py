# -*- coding: utf-8 -*-
"""Tests for third-party agent authentication routes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from qwenpaw.app.routers import harnesses
from qwenpaw.harnesses.base import HarnessOperationNotSupportedError
from qwenpaw.harnesses.codex.user_input import (
    PendingUserInputNotFoundError,
)
from qwenpaw.harnesses.events import HarnessProvider


@pytest.mark.parametrize(
    ("endpoint", "collection_name"),
    [
        (harnesses.get_harness_models, "models"),
        (harnesses.get_harness_mcp, "servers"),
        (harnesses.get_harness_skills, "skills"),
    ],
)
@pytest.mark.asyncio
async def test_capability_endpoints_degrade_when_codex_cli_is_missing(
    endpoint: Callable[[str, Request], Awaitable[dict]],
    collection_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AsyncMock()
    install_message = (
        "Codex runtime not found. Install qwenpaw[codex] or provide a "
        "standalone Codex CLI."
    )
    adapter.capability_unavailable_message = install_message
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    config = SimpleNamespace(backend="codex", backend_settings={})
    workspace = SimpleNamespace(
        config=config,
        harness_runtime=runtime,
        workspace_dir=tmp_path,
    )
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    response = await endpoint("codex", cast(Request, object()))

    assert response == {
        collection_name: [],
        "message": install_message,
    }
    adapter.models.assert_not_awaited()
    adapter.discover_mcp.assert_not_awaited()
    adapter.discover_skills.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_endpoint_returns_actionable_codex_install_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_message = (
        "Codex runtime not found. Install qwenpaw[codex] or provide a "
        "standalone Codex CLI."
    )
    adapter = AsyncMock()
    adapter.status.return_value = HarnessProvider(
        id="codex",
        name="Codex",
        available=True,
        installed=False,
        error=install_message,
    )
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    workspace = SimpleNamespace(harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    response = await harnesses.post_harness_status(
        "codex",
        harnesses.HarnessStatusRequest(),
        cast(Request, object()),
    )

    assert response["installed"] is False
    assert response["error"] == install_message


@pytest.mark.asyncio
async def test_qoder_logout_returns_structured_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AsyncMock()
    adapter.logout.side_effect = HarnessOperationNotSupportedError(
        "Qoder CLI does not expose a non-interactive logout command.",
    )
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    workspace = SimpleNamespace(harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(HTTPException) as exc_info:
        await harnesses.post_harness_logout(
            "qoder",
            harnesses.HarnessStatusRequest(),
            cast(Request, object()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "logout_not_supported",
        "message": (
            "Qoder CLI does not expose a non-interactive logout command."
        ),
    }


@pytest.mark.asyncio
async def test_claude_login_returns_structured_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = (
        "Claude Code sign-in is an interactive flow that must be completed "
        "on the host."
    )
    adapter = AsyncMock()
    adapter.start_login.side_effect = HarnessOperationNotSupportedError(
        message,
    )
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    workspace = SimpleNamespace(harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(HTTPException) as exc_info:
        await harnesses.post_harness_login(
            "claude",
            harnesses.HarnessLoginRequest(),
            cast(Request, object()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "login_not_supported",
        "message": message,
    }


@pytest.mark.asyncio
async def test_claude_is_a_supported_provider() -> None:
    provider = harnesses._supported_provider("claude")

    assert provider.id == "claude"
    assert provider.coming_soon is False


@pytest.mark.asyncio
async def test_user_input_endpoint_resolves_active_codex_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AsyncMock()
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    config = SimpleNamespace(backend="codex", backend_settings={})
    workspace = SimpleNamespace(config=config, harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )
    body = harnesses.HarnessUserInputRequest(
        session_id="chat-1",
        answers={"choice": {"answers": ["Second"]}},
    )

    response = await harnesses.post_harness_user_input(
        "codex",
        "request-1",
        body,
        cast(Request, object()),
    )

    assert response == {"ok": True}
    runtime.adapter.assert_awaited_once_with("codex", {})
    adapter.resolve_user_input.assert_awaited_once_with(
        "chat-1",
        "request-1",
        {"choice": {"answers": ["Second"]}},
    )


@pytest.mark.asyncio
async def test_user_input_endpoint_reports_an_expired_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AsyncMock()
    adapter.resolve_user_input.side_effect = PendingUserInputNotFoundError(
        "request-1",
    )
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    config = SimpleNamespace(backend="codex", backend_settings={})
    workspace = SimpleNamespace(config=config, harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(HTTPException) as exc_info:
        await harnesses.post_harness_user_input(
            "codex",
            "request-1",
            harnesses.HarnessUserInputRequest(
                session_id="chat-1",
                answers={},
            ),
            cast(Request, object()),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "user_input_expired"


@pytest.mark.asyncio
async def test_user_input_endpoint_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AsyncMock()
    adapter.resolve_user_input.side_effect = (
        HarnessOperationNotSupportedError("not supported")
    )
    runtime = SimpleNamespace(adapter=AsyncMock(return_value=adapter))
    config = SimpleNamespace(backend="claude", backend_settings={})
    workspace = SimpleNamespace(config=config, harness_runtime=runtime)
    monkeypatch.setattr(
        harnesses,
        "get_agent_for_request",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(HTTPException) as exc_info:
        await harnesses.post_harness_user_input(
            "claude",
            "request-1",
            harnesses.HarnessUserInputRequest(
                session_id="chat-1",
                answers={},
            ),
            cast(Request, object()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "user_input_not_supported"
