# -*- coding: utf-8 -*-
"""Projects, keys, validation limits, membership and lifecycle (§6, §8.3-4)."""

import pytest

from conftest import NOW, make_issue, make_project
from project_kanban_core import models, rules
from project_kanban_core.errors import KanbanError


def _codes(fn):
    with pytest.raises(KanbanError) as exc:
        fn()
    return exc.value.code


# ── Keys ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Website rebuild", "WR"),
        ("Q4 report for the board", "QRFT"),
        ("Website", "WEB"),
        ("db migrate", "DM"),
        ("数据迁移", "P1"),
        ("A", "P1"),
        ("  --  ", "P1"),
    ],
)
def test_derive_key(name, expected):
    assert rules.derive_key(name, set()) == expected


def test_derive_key_collisions():
    assert rules.derive_key("Website rebuild", {"WR"}) == "WR2"
    assert rules.derive_key("Website rebuild", {"WR", "WR2"}) == "WR3"
    assert rules.derive_key("数据", {"P1", "P2"}) == "P3"
    for key in ("WR", "WR2", "QRFT9", "WEB", "P1", "P12"):
        assert models.KEY_RE.match(key)


def test_create_project_defaults(board):
    project = make_project(board, members=["alice", "alice", "bob"])
    assert project["key"] == "WR"
    assert [m["agent_id"] for m in project["members"]] == ["alice", "bob"]
    assert project["next_number"] == 1
    assert project["archived"] is False and project["archived_at"] is None
    view = rules.project_view(board, project)
    assert view["counts"] == {s: 0 for s in models.STATUSES}


def test_explicit_key_uppercased_and_unique(board):
    project = make_project(board, key="web")
    assert project["key"] == "WEB"
    assert _codes(lambda: make_project(board, name="Other", key="WEB")) == (
        "PROJECT_KEY_TAKEN"
    )
    for bad in ("W", "1AB", "TOOLONGKEY", "A-B"):
        assert (
            _codes(lambda b=bad: make_project(board, key=b))
            == "VALIDATION_ERROR"
        )


def test_key_unique_including_archived(board):
    project = make_project(board, key="ARC")
    rules.archive_project(board, project["id"], NOW)
    assert (
        _codes(lambda: make_project(board, key="ARC")) == "PROJECT_KEY_TAKEN"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 81},
        {"name": "ok", "description": "x" * 4001},
        {"name": "ok", "members": [""]},
    ],
)
def test_project_validation_limits(board, kwargs):
    body = models.ProjectCreate(**kwargs)
    assert (
        _codes(lambda: rules.create_project(board, body, NOW, lambda _: True))
        == "VALIDATION_ERROR"
    )


def test_project_limits_accept_boundaries(board):
    body = models.ProjectCreate(name="x" * 80, description="d" * 4000)
    project = rules.create_project(board, body, NOW, lambda _: True)
    assert len(project["name"]) == 80


def test_unknown_agents_rejected_when_checkable(board):
    body = models.ProjectCreate(name="P", members=["ghost"])
    assert (
        _codes(lambda: rules.create_project(board, body, NOW, lambda _: False))
        == "AGENT_NOT_FOUND"
    )
    # Unknown (None): accepted (R-MEM-2 fallback).
    project = rules.create_project(board, body, NOW, lambda _: None)
    assert rules.member_ids(project) == ["ghost"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"title": "ok", "description": "x" * 8001},
        {"title": "ok", "language": "fr"},
        {"title": "ok", "status": "review"},
    ],
)
def test_issue_validation_limits(board, kwargs):
    project = make_project(board)
    body = models.IssueCreate(**kwargs)
    assert (
        _codes(lambda: rules.create_issue(board, project["id"], body, NOW))
        == "VALIDATION_ERROR"
    )


def test_issue_numbers_never_reused(board):
    project = make_project(board, members=["alice"])
    first = make_issue(board, project)
    second = make_issue(board, project)
    rules.delete_issue(board, project["id"], second["id"])
    third = make_issue(board, project)
    assert [first["number"], third["number"]] == [1, 3]
    assert rules.issue_view(project, third)["key"] == "WR-3"


def test_create_issue_assignee_rules(board):
    project = make_project(board, members=["alice"])
    assert (
        _codes(lambda: make_issue(board, project, status="todo"))
        == "ASSIGNEE_REQUIRED"
    )
    assert (
        _codes(lambda: make_issue(board, project, assignee="bob"))
        == "ASSIGNEE_NOT_MEMBER"
    )
    issue = make_issue(board, project, assignee="alice", status="todo")
    assert issue["queued_at"] == NOW
    assert issue["language"] == "en"
    backlog = make_issue(board, project, assignee="alice")
    assert backlog["queued_at"] is None


def test_issue_lookup_scoped_to_project(board):
    first = make_project(board, key="ONE")
    second = make_project(board, key="TWO")
    issue = make_issue(board, first)
    assert (
        _codes(lambda: rules.find_issue(board, second["id"], issue["id"]))
        == "ISSUE_NOT_FOUND"
    )
    assert (
        _codes(lambda: rules.find_project(board, "nope"))
        == "PROJECT_NOT_FOUND"
    )


def test_delete_running_issue_refused(board):
    project = make_project(board, members=["alice"])
    issue = make_issue(board, project, assignee="alice")
    rules.start_run(issue, NOW)
    assert (
        _codes(lambda: rules.delete_issue(board, project["id"], issue["id"]))
        == "ISSUE_RUNNING"
    )


# ── Membership ───────────────────────────────────────────────────────
def test_add_members_idempotent(board):
    project = make_project(board, members=["alice"])
    added_at = project["members"][0]["added_at"]
    rules.add_members(
        board, project["id"], ["alice", "bob"], NOW + 5, lambda _: True
    )
    assert rules.member_ids(project) == ["alice", "bob"]
    assert project["members"][0]["added_at"] == added_at  # unchanged
    assert (
        _codes(
            lambda: rules.add_members(
                board, project["id"], [], NOW, lambda _: True
            ),
        )
        == "VALIDATION_ERROR"
    )
    assert (
        _codes(
            lambda: rules.add_members(
                board,
                project["id"],
                ["ghost"],
                NOW,
                lambda _: False,
            ),
        )
        == "AGENT_NOT_FOUND"
    )


def test_remove_member_side_effects(board):
    project = make_project(board, members=["alice", "bob"])
    other = make_project(board, name="Other", members=["alice"])
    backlog = make_issue(board, project, assignee="alice")
    queued = make_issue(board, project, assignee="alice", status="todo")
    reviewed = make_issue(board, project, assignee="alice")
    reviewed["status"] = "review"
    done = make_issue(board, project, assignee="alice")
    done["status"] = "done"
    bobs = make_issue(board, project, assignee="bob", status="todo")
    elsewhere = make_issue(board, other, assignee="alice", status="todo")

    _, unassigned, dequeued = rules.remove_member(
        board,
        project["id"],
        "alice",
        NOW + 1,
    )
    assert (unassigned, dequeued) == (1, 1)
    assert backlog["assignee"] == "" and backlog["status"] == "backlog"
    assert queued["assignee"] == "" and queued["status"] == "backlog"
    assert queued["queued_at"] is None
    assert reviewed["assignee"] == "alice"  # I5: history kept
    assert done["assignee"] == "alice"
    assert bobs["assignee"] == "bob" and bobs["status"] == "todo"
    assert elsewhere["status"] == "todo"  # R-MEM-5
    assert rules.member_ids(project) == ["bob"]


def test_remove_member_busy_and_unknown(board):
    project = make_project(board, members=["alice"])
    other = make_project(board, name="Other", members=["alice"])
    running_elsewhere = make_issue(board, other, assignee="alice")
    rules.start_run(running_elsewhere, NOW)
    # Running in another project does not block removal here...
    rules.remove_member(board, project["id"], "alice", NOW)
    rules.add_members(board, project["id"], ["alice"], NOW, lambda _: True)
    issue = make_issue(board, project, assignee="alice")
    rules.start_run(issue, NOW)
    running_elsewhere["status"] = "review"
    # ...but running here does.
    assert (
        _codes(lambda: rules.remove_member(board, project["id"], "alice", NOW))
        == "MEMBER_BUSY"
    )
    assert (
        _codes(lambda: rules.remove_member(board, project["id"], "zed", NOW))
        == "MEMBER_NOT_FOUND"
    )


# ── Lifecycle ────────────────────────────────────────────────────────
def test_archive_refused_while_running(board):
    project = make_project(board, members=["alice"])
    issue = make_issue(board, project, assignee="alice")
    rules.start_run(issue, NOW)
    assert (
        _codes(lambda: rules.archive_project(board, project["id"], NOW))
        == "PROJECT_HAS_RUNNING"
    )


def test_archived_project_is_read_only(board):
    project = make_project(board, members=["alice"])
    issue = make_issue(board, project, assignee="alice")
    rules.archive_project(board, project["id"], NOW + 1)
    assert project["archived"] is True and project["archived_at"] == NOW + 1
    pid, iid = project["id"], issue["id"]
    attempts = [
        lambda: make_issue(board, project),
        lambda: rules.patch_issue(
            board, pid, iid, models.IssuePatch(title="x"), NOW
        ),
        lambda: rules.delete_issue(board, pid, iid),
        lambda: rules.plan_run(
            board, pid, iid, None, NOW, agent_busy=lambda _a: False
        ),
        lambda: rules.add_members(board, pid, ["bob"], NOW, lambda _: True),
        lambda: rules.remove_member(board, pid, "alice", NOW),
        lambda: rules.patch_project(
            board, pid, models.ProjectPatch(name="x"), NOW
        ),
    ]
    for attempt in attempts:
        assert _codes(attempt) == "PROJECT_ARCHIVED"
    # Archive is idempotent; stop is a harmless no-op.
    rules.archive_project(board, pid, NOW + 2)
    assert project["archived_at"] == NOW + 1
    assert rules.stop_run(board, pid, iid, NOW)[1] is False


def test_unarchive_and_delete(board):
    project = make_project(board, members=["alice"])
    make_issue(board, project)
    assert (
        _codes(lambda: rules.delete_project(board, project["id"]))
        == "PROJECT_NOT_ARCHIVED"
    )
    rules.archive_project(board, project["id"], NOW)
    _, changed = rules.unarchive_project(board, project["id"], NOW + 1)
    assert changed is True and project["archived_at"] is None
    assert rules.unarchive_project(board, project["id"], NOW + 1)[1] is False
    rules.archive_project(board, project["id"], NOW + 2)
    removed = rules.delete_project(board, project["id"])
    assert len(removed) == 1
    assert board["projects"] == [] and board["issues"] == []


def test_patch_project(board):
    project = make_project(board)
    rules.patch_project(
        board,
        project["id"],
        models.ProjectPatch(name=" New name ", description="brief"),
        NOW + 1,
    )
    assert project["name"] == "New name" and project["description"] == "brief"
    assert project["key"] == "WR"  # immutable
    assert (
        _codes(
            lambda: rules.patch_project(
                board,
                project["id"],
                models.ProjectPatch(name=""),
                NOW,
            ),
        )
        == "VALIDATION_ERROR"
    )
