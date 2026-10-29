# -*- coding: utf-8 -*-
"""Agent tools (§12): membership ACL, id-or-key lookup, limits."""

import json

import pytest

from conftest import NOW, make_issue, make_project
from project_kanban_core import rules
from project_kanban_core.tools import KanbanTools, build_tools


@pytest.fixture
async def seeded(store):
    async with store.txn() as board:
        web = make_project(
            board, name="Website", key="WEB", members=["alice", "bob"]
        )
        rpt = make_project(board, name="Report", key="RPT", members=["bob"])
        old = make_project(board, name="Old", key="OLD", members=["alice"])
        mine = make_issue(
            board, web, title="Mine", assignee="alice", now=NOW + 1
        )
        done = make_issue(
            board, web, title="Done", assignee="alice", now=NOW + 2
        )
        done["status"] = "done"
        teammate = make_issue(
            board, web, title="Bob's", assignee="bob", now=NOW + 3
        )
        teammate["status"] = "review"
        teammate["last_run"] = {
            "agent_id": "bob",
            "session_id": "s",
            "result_excerpt": "r" * 900,
            "outcome": "succeeded",
        }
        make_issue(board, rpt, title="Secret", assignee="bob")
        rules.archive_project(board, old["id"], NOW)
    return {
        "web": web,
        "rpt": rpt,
        "old": old,
        "mine": mine,
        "teammate": teammate,
    }


def _tools(store, caller):
    return KanbanTools(store, caller=lambda: caller)


async def test_list_my_projects(store, seeded):
    result = await _tools(store, "alice").list_my_projects()
    assert [p["key"] for p in result["projects"]] == ["WEB"]
    web = result["projects"][0]
    assert web["members"] == ["alice", "bob"]
    assert web["counts"]["backlog"] == 1 and web["counts"]["done"] == 1
    assert [i["key"] for i in web["my_open_issues"]] == ["WEB-1"]
    archived = await _tools(store, "alice").list_my_projects(
        include_archived=True
    )
    assert [p["key"] for p in archived["projects"]] == ["WEB", "OLD"]
    json.dumps(archived)  # JSON-serialisable (R-TOOL-3)


async def test_description_truncated(store, seeded):
    async with store.txn() as board:
        rules.find_project(board, seeded["web"]["id"])["description"] = (
            "d" * 900
        )
    result = await _tools(store, "alice").list_my_projects()
    assert len(result["projects"][0]["description"]) == 500


@pytest.mark.parametrize("ref", ["WEB", "web", "<id>"])
async def test_list_project_issues_by_key_or_id(store, seeded, ref):
    ref = seeded["web"]["id"] if ref == "<id>" else ref
    result = await _tools(store, "alice").list_project_issues(ref)
    assert result["project"]["key"] == "WEB"
    keys = [i["key"] for i in result["issues"]]
    assert keys == ["WEB-3", "WEB-2", "WEB-1"]  # newest updated_at first
    teammate = result["issues"][0]
    assert teammate["assignee"] == "bob"
    assert len(teammate["result_excerpt"]) == 500


async def test_list_project_issues_filters_and_limit(store, seeded):
    tools = _tools(store, "alice")
    result = await tools.list_project_issues("WEB", status="review")
    assert [i["key"] for i in result["issues"]] == ["WEB-3"]
    result = await tools.list_project_issues("WEB", assignee="alice", limit=1)
    assert result["total"] == 2 and len(result["issues"]) == 1
    result = await tools.list_project_issues("WEB", limit=10_000)
    assert len(result["issues"]) == 3
    assert (await tools.list_project_issues("WEB", status="nope"))[
        "error"
    ] == ("INVALID_ARGUMENT")
    assert (await tools.list_project_issues("WEB", limit="x"))["error"] == (
        "INVALID_ARGUMENT"
    )


@pytest.mark.parametrize("ref", ["RPT", "<rpt-id>", "NOPE", "missing-id"])
async def test_non_member_cannot_see_project(store, seeded, ref):
    ref = seeded["rpt"]["id"] if ref == "<rpt-id>" else ref
    result = await _tools(store, "alice").list_project_issues(ref)
    # Same answer whether or not the project exists (R-TOOL-1).
    assert result["error"] == "NOT_A_MEMBER"
    assert "issues" not in result


async def test_unknown_caller(store, seeded):
    tools = _tools(store, "")
    for call in (
        tools.list_my_projects(),
        tools.list_project_issues("WEB"),
        tools.get_issue("WEB-1"),
        tools.list_my_issues(),
    ):
        assert (await call)["error"] == "UNKNOWN_CALLER"


async def test_get_issue_by_key_or_id(store, seeded):
    tools = _tools(store, "alice")
    by_key = await tools.get_issue("web-3")
    by_id = await tools.get_issue(seeded["teammate"]["id"])
    assert by_key == by_id
    assert by_key["key"] == "WEB-3"
    assert len(by_key["last_run"]["result_excerpt"]) == 900  # full excerpt
    assert (await tools.get_issue("RPT-1"))["error"] == "NOT_A_MEMBER"
    assert (await tools.get_issue("WEB-99"))["error"] == "NOT_A_MEMBER"


async def test_list_my_issues(store, seeded):
    result = await _tools(store, "bob").list_my_issues()
    assert sorted(i["key"] for i in result["issues"]) == ["RPT-1", "WEB-3"]
    result = await _tools(store, "bob").list_my_issues(status="review")
    assert [i["key"] for i in result["issues"]] == ["WEB-3"]
    assert (await _tools(store, "bob").list_my_issues(status="x"))[
        "error"
    ] == ("INVALID_ARGUMENT")


async def test_board_unavailable(store, monkeypatch):
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    store.data_dir.mkdir(parents=True)
    store.path.write_text("broken", encoding="utf-8")
    result = await _tools(store, "alice").list_my_projects()
    assert result["error"] == "BOARD_UNAVAILABLE"


async def test_build_tools_specs(store, seeded, monkeypatch):
    specs = build_tools(store)
    names = [s["name"] for s in specs]
    assert names == [
        "project_kanban_list_my_projects",
        "project_kanban_list_project_issues",
        "project_kanban_get_issue",
        "project_kanban_list_my_issues",
    ]
    for spec in specs:
        assert spec["func"].__name__ == spec["name"]
        assert spec["func"].__doc__ and spec["description"]
    monkeypatch.setattr(
        "project_kanban_core.tools.current_caller",
        lambda: "alice",
    )
    # current_caller is bound at KanbanTools construction; rebuild.
    specs = build_tools(store)
    result = await specs[1]["func"](project="WEB", limit=2)
    assert len(result["issues"]) == 2
    assert (await specs[0]["func"]())["projects"][0]["key"] == "WEB"
    assert (await specs[2]["func"](issue="WEB-1"))["title"] == "Mine"
    assert (await specs[3]["func"]())["agent_id"] == "alice"


def test_current_caller_reads_agent_context():
    from qwenpaw.app.agent_context import set_current_agent_id

    from project_kanban_core.tools import current_caller

    set_current_agent_id("alice")
    try:
        assert current_caller() == "alice"
    finally:
        set_current_agent_id("")


async def test_unbound_agent_context_is_unknown_caller(store, seeded):
    """P1: no fallback to the configured active agent when nothing is bound."""
    import asyncio
    import contextvars

    from qwenpaw.app.agent_context import (
        get_current_agent_id,
        set_current_agent_id,
    )

    tools = KanbanTools(store)  # real caller resolution
    specs = {s["name"]: s["func"] for s in build_tools(store)}

    async def unbound():
        return (
            await tools.list_my_projects(),
            await specs["project_kanban_list_project_issues"](project="WEB"),
            get_current_agent_id(),
        )

    mine, issues, fallback = await asyncio.create_task(
        unbound(),
        context=contextvars.Context(),
    )
    assert mine["error"] == "UNKNOWN_CALLER"
    assert issues["error"] == "UNKNOWN_CALLER"
    assert fallback  # the host API would have answered with some agent

    async def bound():
        set_current_agent_id("alice")
        return await tools.list_my_projects()

    result = await asyncio.create_task(bound(), context=contextvars.Context())
    assert [p["key"] for p in result["projects"]] == ["WEB"]
