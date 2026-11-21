# -*- coding: utf-8 -*-
"""Rewinding a chat to just before one of its user prompts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from qwenpaw.app.chats.api import RewindRequest
from qwenpaw.app.chats.models import ChatSpec
from qwenpaw.app.chats.rewind import RewindError, rewind_chat
from qwenpaw.app.chats.session import SafeJSONSession
from qwenpaw.app.task_tracker import TaskTracker
from qwenpaw.checkpoints.policy import session_file_path
from qwenpaw.checkpoints.runtime import RUNTIME

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        shutil.which("git") is None,
        reason="checkpoint tests require git",
    ),
]

SESSION_ID = "session-1"
USER_ID = "user"
CHANNEL = "console"
IDS = {"session_id": SESSION_ID, "user_id": USER_ID, "channel": CHANNEL}


@pytest.fixture(autouse=True)
async def _clear_checkpoint_registry():
    await RUNTIME.flush_and_close_all()
    yield
    await RUNTIME.flush_and_close_all()


@pytest.fixture
def workspace(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_dir=tmp_path,
        config=SimpleNamespace(backend="qwenpaw"),
        task_tracker=TaskTracker(),
        session=SafeJSONSession(save_dir=str(tmp_path / "sessions")),
    )


@pytest.fixture
def chat() -> ChatSpec:
    return ChatSpec(name="chat", **IDS)


def _turn(n: int) -> list[dict]:
    return [
        {
            "id": f"u{n}",
            "role": "user",
            "content": [{"type": "text", "text": f"prompt {n}"}],
            "metadata": {"qwenpaw_client_message_id": f"c{n}"},
        },
        {
            "id": f"a{n}",
            "role": "assistant",
            "content": [{"type": "text", "text": f"reply {n}"}],
        },
    ]


def _session_path(workspace) -> Path:
    return session_file_path(workspace.workspace_dir, **IDS)


def _save(workspace, turns: int) -> None:
    context = [msg for n in range(1, turns + 1) for msg in _turn(n)]
    path = _session_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "state": {"summary": "", "context": context},
                    "scroll": {"archived": ["a1"]},
                    "mode_state": {"mode": "default"},
                },
            },
        ),
        encoding="utf-8",
    )


def _context_ids(workspace) -> list[str]:
    data = json.loads(_session_path(workspace).read_text(encoding="utf-8"))
    return [msg["id"] for msg in data["agent"]["state"]["context"]]


async def _rewind(workspace, chat, **kwargs):
    params = {
        "message_id": None,
        "client_message_id": None,
        "include_files": False,
        "dry_run": False,
        **kwargs,
    }
    return await rewind_chat(workspace, chat, **params)


async def test_rewind_cuts_the_prompt_and_everything_after(
    workspace,
    chat,
) -> None:
    _save(workspace, 3)

    result = await _rewind(workspace, chat, message_id="u2")

    assert result.prompt_text == "prompt 2"
    assert result.removed_count == 4
    assert result.files_available is False
    assert _context_ids(workspace) == ["u1", "a1"]
    data = json.loads(_session_path(workspace).read_text(encoding="utf-8"))
    assert "scroll" not in data["agent"]
    assert data["agent"]["mode_state"] == {"mode": "default"}


async def test_rewind_finds_prompt_by_client_message_id(
    workspace,
    chat,
) -> None:
    _save(workspace, 2)

    result = await _rewind(workspace, chat, client_message_id="c1")

    assert result.prompt_text == "prompt 1"
    assert _context_ids(workspace) == []


async def test_dry_run_changes_nothing(workspace, chat) -> None:
    _save(workspace, 2)

    result = await _rewind(workspace, chat, message_id="u2", dry_run=True)

    assert result.dry_run is True
    assert result.removed_count == 2
    assert _context_ids(workspace) == ["u1", "a1", "u2", "a2"]


async def test_rewind_refuses_assistant_and_unknown_messages(
    workspace,
    chat,
) -> None:
    _save(workspace, 1)

    for message_id in ("a1", "missing"):
        with pytest.raises(RewindError) as exc_info:
            await _rewind(workspace, chat, message_id=message_id)
        assert exc_info.value.status == 404


async def test_rewind_refuses_running_chat(workspace, chat) -> None:
    _save(workspace, 1)

    async def status(_run_key: str) -> str:
        return "running"

    workspace.task_tracker = SimpleNamespace(get_status=status)

    with pytest.raises(RewindError) as exc_info:
        await _rewind(workspace, chat, message_id="u1")
    assert exc_info.value.status == 409
    assert _context_ids(workspace) == ["u1", "a1"]


async def test_rewind_refuses_harness_agents(workspace, chat) -> None:
    _save(workspace, 1)
    workspace.config.backend = "codex"

    with pytest.raises(RewindError) as exc_info:
        await _rewind(workspace, chat, message_id="u1")
    assert exc_info.value.status == 409


def test_request_needs_a_target() -> None:
    with pytest.raises(ValidationError):
        RewindRequest()
    assert RewindRequest(client_message_id="c1").client_message_id == "c1"


async def _enable_auto(workspace) -> None:
    service = await RUNTIME.get_for_workspace_async(workspace)
    await service.set_auto_enabled(True)


async def _end_turn(workspace) -> None:
    await RUNTIME.auto_snapshot(workspace, query_text=None, **IDS)


async def test_rewind_restores_files_from_before_the_prompt(
    workspace,
    chat,
) -> None:
    await _enable_auto(workspace)
    notes = workspace.workspace_dir / "notes.txt"
    extra = workspace.workspace_dir / "extra.txt"

    await RUNTIME.baseline_snapshot(workspace, **IDS)
    notes.write_text("v1", encoding="utf-8")
    _save(workspace, 1)
    await _end_turn(workspace)
    notes.write_text("v2", encoding="utf-8")
    extra.write_text("new", encoding="utf-8")
    _save(workspace, 2)
    await _end_turn(workspace)

    preview = await _rewind(
        workspace,
        chat,
        message_id="u2",
        include_files=True,
        dry_run=True,
    )
    assert preview.files_available is True
    assert preview.file_paths == ["extra.txt", "notes.txt"]
    assert notes.read_text(encoding="utf-8") == "v2"

    result = await _rewind(
        workspace,
        chat,
        message_id="u2",
        include_files=True,
    )

    assert result.files_restored is True
    assert result.pre_restore_ref
    assert notes.read_text(encoding="utf-8") == "v1"
    assert not extra.exists()
    assert _context_ids(workspace) == ["u1", "a1"]


async def test_rewind_first_prompt_uses_the_baseline(
    workspace,
    chat,
) -> None:
    await _enable_auto(workspace)
    notes = workspace.workspace_dir / "notes.txt"

    await RUNTIME.baseline_snapshot(workspace, **IDS)
    notes.write_text("v1", encoding="utf-8")
    _save(workspace, 1)
    await _end_turn(workspace)

    result = await _rewind(
        workspace,
        chat,
        message_id="u1",
        include_files=True,
    )

    assert result.files_restored is True
    assert not notes.exists()
    assert _context_ids(workspace) == []


async def test_rewind_keeps_files_unless_asked(workspace, chat) -> None:
    await _enable_auto(workspace)
    notes = workspace.workspace_dir / "notes.txt"
    _save(workspace, 1)
    await _end_turn(workspace)
    notes.write_text("changed", encoding="utf-8")
    _save(workspace, 2)

    result = await _rewind(workspace, chat, message_id="u2")

    assert result.files_available is True
    assert result.file_paths == ["notes.txt"]
    assert result.files_restored is False
    assert notes.read_text(encoding="utf-8") == "changed"


async def test_no_checkpoint_before_prompt_means_no_files(
    workspace,
    chat,
) -> None:
    await _enable_auto(workspace)
    _save(workspace, 2)
    await _end_turn(workspace)

    result = await _rewind(workspace, chat, message_id="u2")

    assert result.files_available is False
    assert result.file_paths == []


async def test_baseline_only_for_sessions_without_checkpoints(
    workspace,
) -> None:
    await _enable_auto(workspace)
    service = await RUNTIME.get_for_workspace_async(workspace)

    await RUNTIME.baseline_snapshot(workspace, **IDS)
    _save(workspace, 1)
    await RUNTIME.baseline_snapshot(workspace, **IDS)

    entries = await service.timeline(**IDS)
    assert [entry.last_message_id for entry in entries] == [""]
