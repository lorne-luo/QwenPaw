# -*- coding: utf-8 -*-
"""Verify QwenPaw events are forwarded to the paw-notify CLI."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app import mobile_push, notification_store

pytestmark = [pytest.mark.unit]


class FakeProcess:
    def __init__(self, sent, returncode=0, stderr=b""):
        self._sent = sent
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self, data):
        self._sent.append(json.loads(data))
        return b"", self._stderr

    def kill(self):
        pass


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setenv("PAW_NOTIFY_BIN", "/usr/local/bin/paw-notify")

    async def fake_exec(*args, **kwargs):
        assert args == ("/usr/local/bin/paw-notify", "send", "--json")
        return FakeProcess(calls)

    monkeypatch.setattr(mobile_push.asyncio, "create_subprocess_exec", fake_exec)
    yield calls
    mobile_push.set_registry(None)


def use_chat_manager(manager):
    workspace = SimpleNamespace(chat_manager=manager)
    mobile_push.set_registry(SimpleNamespace(agents={"agent-a": workspace}))


async def drain():
    await asyncio.gather(*list(mobile_push._tasks))


def approval(**overrides):
    fields = {
        "channel": "console",
        "owner_agent_id": "agent-a",
        "agent_id": "agent-a",
        "root_session_id": "console:user",
        "session_id": "console:user",
        "tool_name": "execute_shell_command",
        "result_summary": "rm -rf build",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def test_conversation_uses_chat_name_and_chat_id(sent):
    manager = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(name="Plan trip")),
    )
    use_chat_manager(manager)

    notification_store.publish(
        category="conversation",
        agent_id="agent-a",
        title="QwenPaw",
        body="",
        session_id="chat-1",
    )
    await drain()

    assert sent == [
        {
            "source": "qwenpaw",
            "category": "conversation",
            "agent_id": "agent-a",
            "title": "回复完成",
            "body": "Plan trip",
            "chat_id": "chat-1",
        },
    ]
    manager.get_chat.assert_awaited_once_with("chat-1")


async def test_inbox_event_passes_title_and_body_without_chat(sent):
    mobile_push.publish_event(
        category="cron",
        agent_id="agent-a",
        title="Daily report",
        body="All good",
        session_id=None,
    )
    await drain()

    assert sent == [
        {
            "source": "qwenpaw",
            "category": "cron",
            "agent_id": "agent-a",
            "title": "Daily report",
            "body": "All good",
        },
    ]


async def test_console_approval_resolves_chat_id(sent):
    manager = SimpleNamespace(
        get_chat_id_by_session=AsyncMock(return_value="chat-9"),
    )
    use_chat_manager(manager)

    mobile_push.publish_approval(approval())
    await drain()

    assert sent == [
        {
            "source": "qwenpaw",
            "category": "approval",
            "agent_id": "agent-a",
            "title": "需要审批：execute_shell_command",
            "body": "rm -rf build",
            "chat_id": "chat-9",
            "priority": "high",
        },
    ]
    manager.get_chat_id_by_session.assert_awaited_once_with(
        "console:user",
        "console",
    )


async def test_approval_from_unloaded_agent_has_no_chat_id(sent):
    mobile_push.set_registry(SimpleNamespace(agents={}))

    mobile_push.publish_approval(approval())
    await drain()

    assert "chat_id" not in sent[0]


async def test_non_console_approval_is_not_pushed(sent):
    mobile_push.publish_approval(approval(channel="telegram"))
    await drain()

    assert not sent


async def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("PAW_NOTIFY_BIN", raising=False)

    async def fail(*args, **kwargs):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(mobile_push.asyncio, "create_subprocess_exec", fail)
    mobile_push.publish_event(
        category="cron",
        agent_id="agent-a",
        title="t",
        body="b",
        session_id=None,
    )
    await drain()


def test_no_running_loop_is_a_noop(sent):
    mobile_push.publish_event(
        category="cron",
        agent_id="agent-a",
        title="t",
        body="b",
        session_id=None,
    )

    assert not mobile_push._tasks
    assert not sent


async def test_cli_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setenv("PAW_NOTIFY_BIN", "/usr/local/bin/paw-notify")

    async def failing_exec(*args, **kwargs):
        return FakeProcess([], returncode=1, stderr=b"HTTP 401")

    monkeypatch.setattr(
        mobile_push.asyncio,
        "create_subprocess_exec",
        failing_exec,
    )
    mobile_push.publish_event(
        category="mail",
        agent_id="agent-a",
        title="t",
        body="b",
        session_id=None,
    )
    await drain()

    assert "HTTP 401" in caplog.text
