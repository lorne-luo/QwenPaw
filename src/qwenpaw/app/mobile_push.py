# -*- coding: utf-8 -*-
"""Forward QwenPaw events to the Paw Android app through ``paw-notify``.

Disabled unless ``PAW_NOTIFY_BIN`` points at the ``paw-notify`` CLI, which
sends an FCM data message to the app. Sending is fire-and-forget: failures
are logged and never affect the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 30
_registry: Any = None
_tasks: set[asyncio.Task] = set()


def set_registry(registry: Any) -> None:
    """Keep the workspace registry so chat ids and names can be resolved."""
    global _registry
    _registry = registry


def publish_event(
    *,
    category: str,
    agent_id: str,
    title: str,
    body: str,
    session_id: str | None,
) -> None:
    """Push a notification-store event (conversation, cron, heartbeat, mail).

    For ``conversation`` events ``session_id`` already holds the chat id.
    """
    _spawn(_push_event(category, agent_id, title, body, session_id))


def publish_approval(pending: Any) -> None:
    """Push a newly created console approval request."""
    if pending.channel != "console":
        # Other channels notify through their own transport.
        return
    _spawn(_push_approval(pending))


def _spawn(coro) -> None:
    if not os.environ.get("PAW_NOTIFY_BIN"):
        coro.close()
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _push_event(category, agent_id, title, body, session_id) -> None:
    fields = {"category": category, "agent_id": agent_id}
    if category == "conversation":
        chat = await _get_chat(agent_id, session_id) if session_id else None
        fields.update(
            title="回复完成",
            body=chat.name if chat else "",
            chat_id=session_id,
        )
    else:
        fields.update(title=title or category, body=body)
    await _send(fields)


async def _push_approval(pending: Any) -> None:
    chat_id = await _find_chat_id(
        pending.owner_agent_id or pending.agent_id,
        pending.root_session_id or pending.session_id,
        pending.channel,
    )
    await _send(
        {
            "category": "approval",
            "agent_id": pending.owner_agent_id or pending.agent_id,
            "title": f"需要审批：{pending.tool_name}",
            "body": pending.result_summary or "点击查看并批准或拒绝",
            "chat_id": chat_id,
            "priority": "high",
        },
    )


def _chat_manager(agent_id: str) -> Any:
    # Only look at agents that are already running: never start one here.
    workspace = getattr(_registry, "agents", {}).get(agent_id)
    return getattr(workspace, "chat_manager", None)


async def _get_chat(agent_id: str, chat_id: str) -> Any:
    manager = _chat_manager(agent_id)
    if manager is None:
        return None
    try:
        return await manager.get_chat(chat_id)
    except Exception:  # pylint: disable=broad-except
        logger.debug("mobile push: chat lookup failed", exc_info=True)
        return None


async def _find_chat_id(agent_id: str, session_id: str, channel: str):
    manager = _chat_manager(agent_id)
    if manager is None or not session_id:
        return None
    try:
        return await manager.get_chat_id_by_session(session_id, channel)
    except Exception:  # pylint: disable=broad-except
        logger.debug("mobile push: chat id lookup failed", exc_info=True)
        return None


async def _send(fields: dict[str, Any]) -> None:
    payload = {"source": "qwenpaw"}
    payload.update({key: value for key, value in fields.items() if value})
    try:
        proc = await asyncio.create_subprocess_exec(
            os.environ["PAW_NOTIFY_BIN"],
            "send",
            "--json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()),
                _SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if proc.returncode:
            logger.warning(
                "mobile push failed (exit %s): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip()[:300],
            )
    except Exception:  # pylint: disable=broad-except
        logger.warning("mobile push failed", exc_info=True)
