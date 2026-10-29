# -*- coding: utf-8 -*-
"""Approvals (§10.3): exact session match, ownership, unavailability."""

import dataclasses

import pytest

from conftest import NOW, make_issue, make_project
from project_kanban_core import approvals, rules
from project_kanban_core.errors import KanbanError


@dataclasses.dataclass
class Pending:
    request_id: str
    session_id: str
    root_session_id: str
    tool_name: str = "execute_shell_command"
    agent_id: str = "alice"
    severity: str = "high"
    status: str = "pending"
    created_at: float = NOW
    exact: bool = False
    expired: bool = False


class FakeService:
    def __init__(self, pending):
        self.pending = {p.request_id: p for p in pending}
        self.resolved = []
        self.cancelled = []

    async def list_pending_by_session(self, session_id):
        return [p for p in self.pending.values() if p.session_id == session_id]

    async def get_pending_by_root_session(self, root_session_id):
        return [
            p
            for p in self.pending.values()
            if p.root_session_id == root_session_id
        ]

    async def get_request(self, request_id):
        return self.pending.get(request_id)

    async def resolve_request(self, request_id, decision):
        pending = self.pending[request_id]
        if pending.exact:
            raise PermissionError("exact requester")
        self.resolved.append((request_id, decision))
        self.pending.pop(request_id)
        # Host contract: None when the request had expired (TIMEOUT).
        return None if pending.expired else pending

    async def cancel_all_pending_by_root_session(self, root_session_id):
        self.cancelled.append(root_session_id)
        return 1


def _api(pending):
    return approvals.ApprovalApi(
        service=FakeService(pending),
        display_fields=lambda p: {"display_name": p.tool_name.upper()},
        approved="APPROVED",
        denied="DENIED",
    )


@pytest.fixture
def setup(board):
    web = make_project(board, key="WEB", members=["alice", "bob"])
    other = make_project(board, key="OTH", members=["alice", "bob"])
    running = make_issue(board, web, assignee="alice")
    rules.start_run(running, NOW)
    other_running = make_issue(board, other, assignee="bob")
    rules.start_run(other_running, NOW)
    sid = rules.session_id_for(running["id"])
    other_sid = rules.session_id_for(other_running["id"])
    pending = [
        Pending("r-own", sid, sid),
        Pending("r-child", "child-session", sid),  # spawned sub-agent
        Pending("r-other", other_sid, other_sid),
        # Suffix match (template behaviour) must NOT match: exact only.
        Pending("r-suffix", "x:" + running["id"], "x:" + running["id"]),
        Pending("r-done", sid, sid, status="approved"),
    ]
    return board, web, other, running, _api(pending)


async def test_list_exact_session_match(setup):
    board, web, _, running, api = setup
    result = await approvals.list_project(api, board, web["id"])
    assert list(result) == [running["id"]]
    ids = [a["request_id"] for a in result[running["id"]]]
    assert sorted(ids) == ["r-child", "r-own"]
    first = result[running["id"]][0]
    assert set(first) >= {
        "request_id",
        "session_id",
        "root_session_id",
        "tool_name",
        "agent_id",
        "severity",
        "display_name",
        "created_at",
    }


async def test_resolve_owned_request(setup):
    board, web, _, _, api = setup
    assert await approvals.resolve(api, board, web["id"], "r-own", True) == {
        "ok": True,
        "tool_name": "execute_shell_command",
    }
    assert await approvals.resolve(api, board, web["id"], "r-child", False)
    assert api.service.resolved == [
        ("r-own", "APPROVED"),
        ("r-child", "DENIED"),
    ]


@pytest.mark.parametrize("rid", ["r-other", "r-suffix", "r-done", "missing"])
async def test_foreign_or_stale_request_rejected(setup, rid):
    board, web, _, _, api = setup
    with pytest.raises(KanbanError) as exc:
        await approvals.resolve(api, board, web["id"], rid, True)
    assert exc.value.code == "APPROVAL_NOT_FOUND"
    assert exc.value.status_code == 404
    assert api.service.resolved == []


async def test_request_of_stopped_issue_rejected(setup):
    board, web, _, running, api = setup
    rules.stop_run(board, web["id"], running["id"], NOW)
    with pytest.raises(KanbanError):
        await approvals.resolve(api, board, web["id"], "r-own", True)


async def test_exact_requester_policy_maps_to_404(setup):
    board, web, _, _, api = setup
    api.service.pending["r-own"].exact = True
    with pytest.raises(KanbanError) as exc:
        await approvals.resolve(api, board, web["id"], "r-own", True)
    assert exc.value.code == "APPROVAL_NOT_FOUND"


async def test_unavailable_service(setup):
    board, web, _, _, _ = setup
    assert await approvals.list_project(None, board, web["id"]) == {}
    assert await approvals.summary(None, board) == {}
    assert await approvals.cancel_for_session(None, "s") == 0
    with pytest.raises(KanbanError) as exc:
        await approvals.resolve(None, board, web["id"], "r-own", True)
    assert exc.value.code == "APPROVAL_UNAVAILABLE"
    assert exc.value.status_code == 501


async def test_summary_and_cancel(setup):
    board, web, other, running, api = setup
    assert await approvals.summary(api, board) == {
        web["id"]: 2,
        other["id"]: 1,
    }
    sid = rules.session_id_for(running["id"])
    assert await approvals.cancel_for_session(api, sid) == 1
    assert api.service.cancelled == [sid]


async def test_stop_cancels_pending_approvals(engine, hub, store):
    import time

    from conftest import Gate, wait_until
    from project_kanban_core import models

    service_api = _api([])
    engine.approval_api = lambda: service_api
    hub.gate = Gate()
    async with store.txn() as b:
        project = rules.create_project(
            b,
            models.ProjectCreate(name="W", key="WEB", members=["alice"]),
            time.time(),
            lambda _: True,
        )
        issue = rules.create_issue(
            b,
            project["id"],
            models.IssueCreate(title="t", assignee="alice", status="todo"),
            time.time(),
        )
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: engine.runner.is_live(issue["id"]))
    await engine.stop_issue(project["id"], issue["id"])
    assert service_api.service.cancelled == [rules.session_id_for(issue["id"])]


def test_load_api_against_host():
    """The real host modules import and expose the public API we use."""
    api = approvals.load_api()
    assert api is not None
    for name in (
        "list_pending_by_session",
        "get_pending_by_root_session",
        "get_request",
        "resolve_request",
        "cancel_all_pending_by_root_session",
    ):
        assert callable(getattr(api.service, name))


async def test_expired_request_is_not_reported_ok(setup):
    """P2: resolve_request() returns None for an expired request; the host
    has already resolved it as TIMEOUT, so approve must not say ok."""
    board, web, _, _, api = setup
    api.service.pending["r-own"].expired = True
    with pytest.raises(KanbanError) as exc:
        await approvals.resolve(api, board, web["id"], "r-own", True)
    assert exc.value.code == "APPROVAL_NOT_FOUND"
    assert exc.value.status_code == 404
    assert "expired" in exc.value.message
