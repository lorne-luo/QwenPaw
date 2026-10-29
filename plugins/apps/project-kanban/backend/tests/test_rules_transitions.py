# -*- coding: utf-8 -*-
"""State machine (§8.2): every (from, to) pair, table-driven."""

import itertools

import pytest

from conftest import NOW, make_issue, make_project
from project_kanban_core import models, rules
from project_kanban_core.errors import KanbanError

STATUSES = models.STATUSES

# Allowed through PATCH, with the expected queued_at behaviour.
PATCH_ALLOWED = {
    ("backlog", "todo"): "set",
    ("review", "todo"): "set",
    ("done", "todo"): "set",
    ("todo", "backlog"): "clear",
    ("review", "done"): "none",
    ("backlog", "done"): "clear",
    ("todo", "done"): "clear",
    ("done", "review"): "none",
    ("review", "backlog"): "none",
    ("done", "backlog"): "none",
}


def _board_busy(board):
    return lambda agent: rules.running_issue_of(board, agent) is not None


def _never(_agent):
    return False


def _issue_in(board, status, assignee="alice"):
    project = (
        board["projects"][0]
        if board["projects"]
        else make_project(
            board,
            members=["alice", "bob"],
        )
    )
    issue = make_issue(board, project, assignee=assignee)
    issue["status"] = status
    if status == "todo":
        issue["queued_at"] = NOW
    if status == "in_progress":
        rules.start_run(issue, NOW)
    return project, issue


@pytest.mark.parametrize(
    "current,target",
    [(a, b) for a, b in itertools.product(STATUSES, STATUSES) if a != b],
)
def test_patch_transition_table(board, current, target):
    project, issue = _issue_in(board, current)
    body = models.IssuePatch(status=target)
    if (current, target) in PATCH_ALLOWED:
        result, wake = rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            body,
            NOW + 5,
        )
        assert result["status"] == target
        expectation = PATCH_ALLOWED[(current, target)]
        if expectation == "set":
            assert result["queued_at"] == NOW + 5
            assert wake is True
        elif expectation == "clear":
            assert result["queued_at"] is None
            assert wake is False
        return
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(board, project["id"], issue["id"], body, NOW + 5)
    expected = (
        "ISSUE_RUNNING" if current == "in_progress" else "INVALID_TRANSITION"
    )
    assert exc.value.code == expected
    assert exc.value.status_code == 409
    assert issue["status"] == current  # nothing applied


@pytest.mark.parametrize("current", ["backlog", "review", "done"])
def test_to_todo_requires_member_assignee(board, current):
    project, issue = _issue_in(board, current, assignee="")
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            models.IssuePatch(status="todo"),
            NOW,
        )
    assert exc.value.code == "ASSIGNEE_REQUIRED"


def test_to_todo_rejects_former_member(board):
    project, issue = _issue_in(board, "review")
    issue["assignee"] = "ghost"  # kept from a member who left (I5)
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            models.IssuePatch(status="todo"),
            NOW,
        )
    assert exc.value.code == "ASSIGNEE_NOT_MEMBER"


def test_patch_cannot_edit_running_issue(board):
    project, issue = _issue_in(board, "in_progress")
    for body in (
        models.IssuePatch(title="x"),
        models.IssuePatch(description="x"),
        models.IssuePatch(language="zh"),
        models.IssuePatch(assignee="bob"),
    ):
        with pytest.raises(KanbanError) as exc:
            rules.patch_issue(board, project["id"], issue["id"], body, NOW)
        assert exc.value.code == "ISSUE_RUNNING"


@pytest.mark.parametrize("status", ["backlog", "todo", "review", "done"])
def test_text_fields_editable_outside_in_progress(board, status):
    project, issue = _issue_in(board, status)
    result, _ = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(title=" New ", description="d", language="zh"),
        NOW + 1,
    )
    assert (result["title"], result["description"], result["language"]) == (
        "New",
        "d",
        "zh",
    )
    assert result["status"] == status


@pytest.mark.parametrize("status", ["review", "done"])
def test_assignee_locked_outside_backlog_todo(board, status):
    project, issue = _issue_in(board, status)
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            models.IssuePatch(assignee="bob"),
            NOW,
        )
    assert exc.value.code == "ASSIGNEE_LOCKED"


def test_assign_on_backlog_does_not_queue(board):
    project, issue = _issue_in(board, "backlog", assignee="")
    result, wake = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(assignee="alice"),
        NOW,
    )
    assert result["status"] == "backlog"  # R-SM-4
    assert result["assignee"] == "alice"
    assert wake is False


def test_reassign_todo_goes_to_end_of_new_queue(board):
    project, issue = _issue_in(board, "todo")
    result, wake = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(assignee="bob"),
        NOW + 9,
    )
    assert result["status"] == "todo"
    assert result["queued_at"] == NOW + 9
    assert wake is True


def test_unassign_todo_moves_to_backlog(board):
    project, issue = _issue_in(board, "todo")
    result, wake = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(assignee=""),
        NOW,
    )
    assert result["status"] == "backlog"
    assert result["queued_at"] is None
    assert wake is False


def test_assignee_must_be_member(board):
    project, issue = _issue_in(board, "backlog", assignee="")
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            models.IssuePatch(assignee="mallory"),
            NOW,
        )
    assert exc.value.code == "ASSIGNEE_NOT_MEMBER"


def test_same_status_and_empty_patch_are_noops(board):
    project, issue = _issue_in(board, "review")
    before = dict(issue)
    result, wake = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(),
        NOW + 1,
    )
    assert result == before and wake is False
    result, wake = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(status="review"),
        NOW + 1,
    )
    assert result["status"] == "review" and wake is False


def test_unknown_status_is_validation_error(board):
    project, issue = _issue_in(board, "backlog")
    with pytest.raises(KanbanError) as exc:
        rules.patch_issue(
            board,
            project["id"],
            issue["id"],
            models.IssuePatch(status="archived"),
            NOW,
        )
    assert exc.value.code == "VALIDATION_ERROR"


# ── /run (backlog/todo/review → in_progress) ─────────────────────────
@pytest.mark.parametrize("status", ["backlog", "todo", "review"])
def test_run_starts_when_agent_idle(board, status):
    project, issue = _issue_in(board, status)
    issue["last_run"] = {"agent_id": "bob", "session_id": "s"}
    result, info = rules.plan_run(
        board,
        project["id"],
        issue["id"],
        "zh",
        NOW + 1,
        agent_busy=_board_busy(board),
    )
    assert result["status"] == "in_progress"
    assert result["queued_at"] is None
    assert result["language"] == "zh"
    run = result["last_run"]
    assert run["agent_id"] == "alice"
    assert run["session_id"] == "pawapp:project-kanban:issue:" + issue["id"]
    assert run["started_at"] == NOW + 1 and run["finished_at"] is None
    assert run["outcome"] is None
    assert info["previous_agent_id"] == "bob"


@pytest.mark.parametrize("status", ["backlog", "todo", "review"])
def test_run_queues_when_agent_busy(board, status):
    project, issue = _issue_in(board, status)
    _, busy = _issue_in(board, "in_progress")
    assert busy["assignee"] == "alice"
    result, info = rules.plan_run(
        board,
        project["id"],
        issue["id"],
        None,
        NOW + 7,
        agent_busy=_board_busy(board),
    )
    assert info is None
    assert result["status"] == "todo"
    expected = NOW if status == "todo" else NOW + 7  # keeps existing queued_at
    assert result["queued_at"] == expected


def test_run_from_done_is_invalid(board):
    project, issue = _issue_in(board, "done")
    with pytest.raises(KanbanError) as exc:
        rules.plan_run(
            board, project["id"], issue["id"], None, NOW, agent_busy=_never
        )
    assert exc.value.code == "INVALID_TRANSITION"


def test_run_on_running_issue(board):
    project, issue = _issue_in(board, "in_progress")
    with pytest.raises(KanbanError) as exc:
        rules.plan_run(
            board, project["id"], issue["id"], None, NOW, agent_busy=_never
        )
    assert exc.value.code == "ISSUE_RUNNING"


def test_run_requires_member_assignee(board):
    project, issue = _issue_in(board, "backlog", assignee="")
    with pytest.raises(KanbanError) as exc:
        rules.plan_run(
            board, project["id"], issue["id"], None, NOW, agent_busy=_never
        )
    assert exc.value.code == "ASSIGNEE_REQUIRED"
    issue["assignee"] = "ghost"
    with pytest.raises(KanbanError) as exc:
        rules.plan_run(
            board, project["id"], issue["id"], None, NOW, agent_busy=_never
        )
    assert exc.value.code == "ASSIGNEE_NOT_MEMBER"


def test_run_rejects_bad_language(board):
    project, issue = _issue_in(board, "backlog")
    with pytest.raises(KanbanError) as exc:
        rules.plan_run(
            board, project["id"], issue["id"], "fr", NOW, agent_busy=_never
        )
    assert exc.value.code == "VALIDATION_ERROR"


# ── /stop and system transitions ─────────────────────────────────────
def test_stop_running_issue(board):
    project, issue = _issue_in(board, "in_progress")
    result, stopped = rules.stop_run(
        board, project["id"], issue["id"], NOW + 3
    )
    assert stopped is True
    assert result["status"] == "backlog"
    assert result["last_run"]["outcome"] == "stopped"
    assert result["last_run"]["finished_at"] == NOW + 3


@pytest.mark.parametrize("status", ["backlog", "todo", "review", "done"])
def test_stop_non_running_is_noop(board, status):
    project, issue = _issue_in(board, status)
    result, stopped = rules.stop_run(board, project["id"], issue["id"], NOW)
    assert stopped is False and result["status"] == status


def test_run_success_goes_to_review_with_excerpt(board):
    _, issue = _issue_in(board, "in_progress")
    long_text = "x" * 2500
    result = rules.finish_run(
        board,
        issue["id"],
        NOW,
        "succeeded",
        None,
        long_text,
        NOW + 9,
    )
    assert result["status"] == "review"
    run = result["last_run"]
    assert run["outcome"] == "succeeded" and run["finished_at"] == NOW + 9
    assert len(run["result_excerpt"]) == 2000
    assert run["result_excerpt"].endswith("…")


def test_run_failure_goes_to_backlog_with_error(board):
    _, issue = _issue_in(board, "in_progress")
    result = rules.finish_run(
        board, issue["id"], NOW, "failed", "boom", None, NOW
    )
    assert result["status"] == "backlog"  # no auto-retry (D2)
    assert result["last_run"]["outcome"] == "failed"
    assert result["last_run"]["error"] == "boom"


def test_finish_ignores_stale_run(board):
    _, issue = _issue_in(board, "in_progress")
    assert (
        rules.finish_run(
            board, issue["id"], NOW - 1, "succeeded", None, "", NOW
        )
        is None
    )
    issue["status"] = "backlog"
    assert (
        rules.finish_run(board, issue["id"], NOW, "succeeded", None, "", NOW)
        is None
    )


def test_orphan_recovery_front_of_queue(board):
    _, orphan = _issue_in(board, "in_progress")
    _, live = _issue_in(board, "in_progress", assignee="bob")
    count = rules.recover_orphans(board, {live["id"]}, NOW + 100)
    assert count == 1
    assert orphan["status"] == "todo"
    assert orphan["queued_at"] == NOW  # last_run.started_at
    assert orphan["last_run"]["outcome"] == "interrupted"
    assert live["status"] == "in_progress"


@pytest.mark.parametrize("current", ["review", "done"])
def test_reopen_drops_former_member_assignee(board, current):
    project, issue = _issue_in(board, current)
    issue["assignee"] = "ghost"  # member left after the run (I5 history)
    result, _ = rules.patch_issue(
        board,
        project["id"],
        issue["id"],
        models.IssuePatch(status="backlog"),
        NOW,
    )
    assert result["status"] == "backlog" and result["assignee"] == ""
    current_member = _issue_in(board, current)[1]
    result, _ = rules.patch_issue(
        board,
        project["id"],
        current_member["id"],
        models.IssuePatch(status="backlog"),
        NOW,
    )
    assert result["assignee"] == "alice"  # current members are kept


def test_run_queues_when_only_the_process_knows_the_agent_is_busy(board):
    """The board is idle, but a stopped run is still unwinding in memory."""
    project, issue = _issue_in(board, "backlog")
    result, info = rules.plan_run(
        board,
        project["id"],
        issue["id"],
        None,
        NOW + 3,
        agent_busy=lambda agent: agent == "alice",
    )
    assert info is None
    assert result["status"] == "todo" and result["queued_at"] == NOW + 3
