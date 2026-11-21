# -*- coding: utf-8 -*-
"""Rewind a chat to just before one of its user prompts.

The conversation is cut at the chosen user message (it and everything after
it are removed) and the prompt text is handed back so the client can put it
in the input box. Optionally, workspace files go back to the auto checkpoint
taken when the conversation last ended at the message before that prompt,
i.e. the state the prompt originally ran against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...checkpoints.models import CheckpointError
from ...checkpoints.policy import message_text
from ...checkpoints.runtime import RUNTIME
from ...runtime.console_turn_state import CLIENT_ID
from ...utils.io_utils import run_sync_io
from .models import ChatSpec


class RewindError(Exception):
    """Rewind refused; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RewindResult:
    """Outcome (or preview, when ``dry_run``) of one rewind."""

    dry_run: bool
    prompt_text: str
    removed_count: int
    files_available: bool
    file_paths: list[str] = field(default_factory=list)
    files_restored: bool = False
    pre_restore_ref: str | None = None


def _find_prompt(
    context: list,
    *,
    message_id: str | None,
    client_message_id: str | None,
) -> int:
    for index, message in enumerate(context):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        metadata = message.get("metadata") or {}
        if message_id and message.get("id") == message_id:
            return index
        if client_message_id and metadata.get(CLIENT_ID) == client_message_id:
            return index
    raise RewindError(404, "The prompt was not found in this chat")


async def _checkpoint_service(workspace: Any):
    """Return the checkpoint service only if this workspace has a store."""
    store = Path(workspace.workspace_dir) / "checkpoints" / "shadow.git"
    if not await run_sync_io(store.is_dir):
        return None
    return await RUNTIME.get_for_workspace_async(workspace)


async def rewind_chat(
    workspace: Any,
    chat: ChatSpec,
    *,
    message_id: str | None,
    client_message_id: str | None,
    include_files: bool,
    dry_run: bool,
) -> RewindResult:
    """Rewind *chat* to just before the given user prompt."""
    if workspace.config.backend != "qwenpaw":
        raise RewindError(409, "Rewind is only supported for QwenPaw agents")
    if await workspace.task_tracker.get_status(chat.id) == "running":
        raise RewindError(409, "Stop the running reply before rewinding")

    session = workspace.session
    ids = {
        "session_id": chat.session_id,
        "user_id": chat.user_id,
        "channel": chat.channel,
    }
    state = await session.get_session_state_dict(**ids)
    agent = state.get("agent") or {}
    agent_state = agent.get("state") or {}
    context = agent_state.get("context") or []
    index = _find_prompt(
        context,
        message_id=message_id,
        client_message_id=client_message_id,
    )
    prompt_text = message_text(context[index].get("content")) or ""

    # The checkpoint taken when the conversation ended at the message
    # before this prompt holds the files this prompt originally saw.
    entry = None
    service = await _checkpoint_service(workspace)
    if service is not None:
        anchor = context[index - 1].get("id", "") if index else ""
        entry = await service.latest_after_message(
            **ids,
            message_id=anchor,
        )

    file_paths: list[str] = []
    files_restored = False
    pre_restore_ref = None
    if entry is not None:
        try:
            preview = await service.restore_with_files(
                target=entry.commit,
                **ids,
                dry_run=True,
            )
            file_paths = list(preview.file_paths)
            if include_files and file_paths and not dry_run:
                restored = await service.restore_with_files(
                    target=entry.commit,
                    **ids,
                    selected_files=tuple(file_paths),
                )
                files_restored = True
                pre_restore_ref = restored.pre_restore_ref
        except CheckpointError as exc:
            raise RewindError(400, str(exc)) from exc

    result = RewindResult(
        dry_run=dry_run,
        prompt_text=prompt_text,
        removed_count=len(context) - index,
        files_available=entry is not None,
        file_paths=file_paths,
        files_restored=files_restored,
        pre_restore_ref=pre_restore_ref,
    )
    if dry_run:
        return result

    # Restoring files also put back the checkpoint's copy of the session;
    # cutting the pre-rewind context keeps every earlier message intact.
    agent_state["context"] = context[:index]
    agent["state"] = agent_state
    # Scroll bookkeeping is derived from the context; let it rebuild, as
    # last-turn regeneration does.
    agent.pop("scroll", None)
    await session.update_session_state(
        key="agent",
        value=agent,
        create_if_not_exist=False,
        **ids,
    )
    return result
