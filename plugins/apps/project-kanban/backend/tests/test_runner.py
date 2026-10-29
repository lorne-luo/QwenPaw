# -*- coding: utf-8 -*-
"""Event mapping, live/replay parity, trace cap, prompt templates."""

import asyncio
import json
import time

from conftest import Gate, message_event, tool_event, wait_until
from project_kanban_core import models, rules
from project_kanban_core.runner import (
    build_prompt,
    event_to_dict,
    map_event,
    trace_to_messages,
)
from qwenpaw.schemas import (
    DataContent,
    Message,
    MessageType,
    Role,
    RunStatus,
    TextContent,
)


def test_map_event_message_and_tools():
    assert map_event(message_event("hi")) == {"type": "message", "text": "hi"}
    assert map_event(tool_event("grep")) == {
        "type": "tool_start",
        "name": "grep",
    }
    assert map_event(tool_event("grep", output=True)) == {
        "type": "tool_done",
        "name": "grep",
    }


def test_map_event_skips_noise():
    in_progress = dict(message_event("x"), status="in_progress")
    content = dict(message_event("x"), object="content")
    reasoning = dict(message_event("x"), type="reasoning")
    user = dict(message_event("x"), role="user")
    internal = tool_event("assistant")
    empty = dict(message_event(""), content=[])
    for event in (
        in_progress,
        content,
        reasoning,
        user,
        internal,
        empty,
        None,
        3,
    ):
        assert map_event(event) is None


def test_map_event_real_schema_objects():
    """The mapper accepts the host's pydantic envelope via event_to_dict."""
    msg = Message(
        type=MessageType.MESSAGE,
        role=Role.ASSISTANT,
        content=[TextContent(text="hello")],
        status=RunStatus.Completed,
    )
    msg.object = "message"
    assert map_event(event_to_dict(msg)) == {
        "type": "message",
        "text": "hello",
    }
    call = Message(
        type=MessageType.FUNCTION_CALL_OUTPUT,
        role=Role.TOOL,
        content=[DataContent(data={"name": "web_search", "output": ""})],
        status=RunStatus.Completed,
    )
    call.object = "message"
    assert map_event(event_to_dict(call)) == {
        "type": "tool_done",
        "name": "web_search",
    }


def test_trace_to_messages_shape():
    messages = trace_to_messages(
        [
            {"type": "tool_start", "name": "a"},
            {"type": "tool_done", "name": "a"},
            {"type": "message", "text": "t"},
        ],
    )
    assert [m["type"] for m in messages] == [
        "plugin_call",
        "plugin_call_output",
        "message",
    ]
    assert all(m["role"] == "assistant" for m in messages)


def _project_issue(language):
    project = {
        "key": "WEB",
        "name": "Website",
        "description": "",
        "members": [],
    }
    issue = {
        "number": 12,
        "title": "Fix nav",
        "description": "",
        "language": language,
    }
    return project, issue


def test_prompt_templates():
    project, issue = _project_issue("en")
    prompt = build_prompt(project, issue, ["Alice (alice)", "bob"])
    assert prompt == (
        'You are a member agent of the project "Website" (WEB).\n\n'
        "Project brief:\n(No brief)\n\n"
        "Project members: Alice (alice), bob\n\n"
        "You have been assigned issue WEB-12:\n"
        "Title: Fix nav\nDescription: (No description)\n\n"
        "To see the other issues of this project and your teammates' "
        "results, call\n"
        'project_kanban_list_project_issues with project="WEB".\n\n'
        "Complete the issue and report the result concisely in English."
    )
    project["description"] = "Rebuild the site"
    issue.update(language="zh", description="导航栏错位")
    prompt = build_prompt(project, issue, ["Alice"])
    assert prompt.startswith(
        "你是项目「Website」(WEB) 的成员智能体。\n\n项目简介:\nRebuild the site"
    )
    assert (
        "你被指派处理任务 WEB-12:\n标题: Fix nav\n描述: 导航栏错位" in prompt
    )
    assert prompt.endswith("请完成该任务，并用简洁的中文汇报你的处理结果。")
    project_missing_lang, issue_missing_lang = _project_issue(None)
    assert build_prompt(
        project_missing_lang, issue_missing_lang, []
    ).startswith(
        "You are",
    )


async def _setup_run(engine, store, hub):
    async with store.txn() as board:
        project = rules.create_project(
            board,
            models.ProjectCreate(name="Web", key="WEB", members=["alice"]),
            time.time(),
            lambda _: True,
        )
        issue = rules.create_issue(
            board,
            project["id"],
            models.IssueCreate(title="t", assignee="alice", status="todo"),
            time.time(),
        )
    engine.capture(hub.ctx())
    return project, issue


async def test_live_and_replay_are_identical(engine, hub, store):
    """D7: a late viewer replays exactly what an early viewer saw live."""
    gate = Gate()
    events = [
        tool_event("grep"),
        tool_event("grep", True),
        message_event("answer"),
    ]

    async def scripted_gate(agent):
        await gate(agent)

    hub.script = lambda agent, msg: events
    hub.gate = scripted_gate
    _, issue = await _setup_run(engine, store, hub)
    await engine.run_pass()
    runner = engine.runner
    early_replay, early = runner.subscribe(issue["id"])
    assert early_replay == [] and early is not None

    received = []

    async def consume():
        async for chunk in early:
            if chunk.startswith("data: "):
                received.append(json.loads(chunk[6:]))

    consumer = asyncio.create_task(consume())
    # Hold the run open after its events to let a late viewer join.
    original_publish = runner._publish
    late = {}

    async def publish(issue_id, event, record=True):
        if not record and "replay" not in late:
            late["replay"], late["channel"] = runner.subscribe(issue_id)
        await original_publish(issue_id, event, record)

    runner._publish = publish
    gate.release("alice")
    await asyncio.wait_for(consumer, 2)
    mapped = [map_event(e) for e in events]
    assert received[:-1] == mapped
    assert received[-1] == {"type": "done"}
    assert late["replay"] == mapped  # same mapper, same order


async def test_trace_is_capped(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    hub.script = lambda agent, msg: [
        message_event(str(n)) for n in range(1500)
    ]
    _, issue = await _setup_run(engine, store, hub)
    await engine.run_pass()
    runner = engine.runner
    seen = {}
    original_publish = runner._publish

    async def publish(issue_id, event, record=True):
        if not record:
            seen["trace"] = runner.trace(issue_id)
        await original_publish(issue_id, event, record)

    runner._publish = publish
    gate.release("alice")
    await wait_until(lambda: "trace" in seen)
    assert len(seen["trace"]) == models.TRACE_MAX
    assert seen["trace"][-1]["text"] == "1499"
    await wait_until(lambda: not runner.is_live(issue["id"]))
    assert runner.trace(issue["id"]) == []  # dropped when the run ends


async def test_stream_of_idle_issue_has_no_channel(engine, hub, store):
    _, issue = await _setup_run(engine, store, hub)
    replay, channel = engine.runner.subscribe(issue["id"])
    assert replay == [] and channel is None


async def test_result_falls_back_to_trace_while_running(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    _, issue = await _setup_run(engine, store, hub)
    await engine.run_pass()
    runner = engine.runner
    await runner._publish(issue["id"], {"type": "message", "text": "partial"})
    current = next(i for i in store.read()["issues"] if i["id"] == issue["id"])
    result = await runner.result(hub.ctx(), current)
    assert result["messages"][0]["content"][0]["text"] == "partial"
    gate.release("alice")
    await wait_until(lambda: not runner.is_live(issue["id"]))
    assert await runner.result(None, {"id": "x", "last_run": None}) == {
        "messages": [],
    }


def test_terminal_failure_on_real_host_envelopes():
    """Same shapes as runtime/envelope.py and harnesses/runtime.py emit."""
    from qwenpaw.schemas import AgentResponse

    from project_kanban_core.runner import terminal_failure

    failed = AgentResponse(status=RunStatus.Failed)
    failed.object = "response"
    failed.error = {"code": "harness_error", "message": "boom"}
    assert terminal_failure(event_to_dict(failed)) == "boom"
    cancelled = AgentResponse(status=RunStatus.Cancelled)
    cancelled.object = "response"
    assert terminal_failure(event_to_dict(cancelled)) == (
        "Run cancelled by the host"
    )
    bare = AgentResponse(status=RunStatus.Failed)
    bare.object = "response"
    assert terminal_failure(event_to_dict(bare)) == "Run failed"
    for status in (
        RunStatus.Created,
        RunStatus.InProgress,
        RunStatus.Completed,
    ):
        ok = AgentResponse(status=status)
        ok.object = "response"
        assert terminal_failure(event_to_dict(ok)) is None
    # A failed *message* (not the response envelope) is not terminal.
    assert terminal_failure(dict(message_event("x"), status="failed")) is None
    assert terminal_failure(None) is None
