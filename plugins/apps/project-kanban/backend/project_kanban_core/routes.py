# -*- coding: utf-8 -*-
"""HTTP routes (requirements §11). Mounted by PawApp under the app prefix."""

import json
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from qwenpaw.pawapp import get_ctx

from . import approvals, models, rules
from .agents import AgentDirectory
from .dispatcher import Dispatcher
from .errors import KanbanError
from .store import Store


def build_router(
    store: Store,
    dispatcher: Dispatcher,
    agents: AgentDirectory,
    approval_api: Callable[[], Optional[approvals.ApprovalApi]] = (
        approvals.load_api
    ),
) -> APIRouter:
    router = APIRouter()
    runner = dispatcher.runner

    async def ctx_dep(ctx: Any = Depends(get_ctx)) -> Any:
        """Every request refreshes the dispatcher's base ctx (R-DSP-7)."""
        dispatcher.capture(ctx)
        return ctx

    async def cleanup_sessions(issues: List[Dict[str, Any]]) -> None:
        base_ctx = dispatcher.base_ctx
        if base_ctx is None:
            return
        for issue in issues:
            run = issue.get("last_run") or {}
            if run.get("agent_id") and run.get("session_id"):
                await runner.delete_session(
                    base_ctx,
                    run["agent_id"],
                    run["session_id"],
                )

    # ── Projects ─────────────────────────────────────────────────────
    @router.get("/projects")
    async def list_projects(
        archived: str = "false",
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        if archived not in ("false", "true", "all"):
            raise KanbanError(
                "VALIDATION_ERROR",
                "archived: must be false, true or all",
            )
        board = store.read()
        projects = [
            rules.project_view(board, p)
            for p in board["projects"]
            if archived == "all"
            or bool(p.get("archived")) == (archived == "true")
        ]
        return {"projects": projects}

    @router.post("/projects", status_code=201)
    async def create_project(
        body: models.ProjectCreate,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project = rules.create_project(
                board,
                body,
                time.time(),
                agents.exists,
            )
            return rules.project_view(board, project)

    @router.get("/projects/{project_id}")
    async def get_project(
        project_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        board = store.read()
        return rules.project_view(board, rules.find_project(board, project_id))

    @router.patch("/projects/{project_id}")
    async def patch_project(
        project_id: str,
        body: models.ProjectPatch,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project = rules.patch_project(board, project_id, body, time.time())
            return rules.project_view(board, project)

    @router.post("/projects/{project_id}/archive")
    async def archive_project(
        project_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project = rules.archive_project(board, project_id, time.time())
            return rules.project_view(board, project)

    @router.post("/projects/{project_id}/unarchive")
    async def unarchive_project(
        project_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project, changed = rules.unarchive_project(
                board,
                project_id,
                time.time(),
            )
            view = rules.project_view(board, project)
        if changed:
            dispatcher.wake()
        return view

    @router.delete("/projects/{project_id}")
    async def delete_project(
        project_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            removed = rules.delete_project(board, project_id)
        await cleanup_sessions(removed)
        return {"ok": True, "deleted_issues": len(removed)}

    # ── Members ──────────────────────────────────────────────────────
    @router.post("/projects/{project_id}/members")
    async def add_members(
        project_id: str,
        body: models.MembersAdd,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project = rules.add_members(
                board,
                project_id,
                body.agent_ids,
                time.time(),
                agents.exists,
            )
            return rules.project_view(board, project)

    @router.delete("/projects/{project_id}/members/{agent_id}")
    async def remove_member(
        project_id: str,
        agent_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            project, unassigned, dequeued = rules.remove_member(
                board,
                project_id,
                agent_id,
                time.time(),
            )
            view = rules.project_view(board, project)
        return {
            "project": view,
            "unassigned": unassigned,
            "dequeued": dequeued,
        }

    # ── Issues ───────────────────────────────────────────────────────
    @router.get("/projects/{project_id}/issues")
    async def list_issues(
        project_id: str,
        status: str = "",
        assignee: str = "",
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        board = store.read()
        project = rules.find_project(board, project_id)
        if status:
            models.clean_status(status)
        issues = [
            rules.issue_view(project, i)
            for i in rules.project_issues(board, project_id)
            if (not status or i["status"] == status)
            and (not assignee or i.get("assignee") == assignee)
        ]
        return {"issues": issues}

    @router.post("/projects/{project_id}/issues", status_code=201)
    async def create_issue(
        project_id: str,
        body: models.IssueCreate,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            issue = rules.create_issue(board, project_id, body, time.time())
            view = rules.issue_view(
                rules.find_project(board, project_id), issue
            )
        if issue["status"] == "todo":
            dispatcher.wake()
        return view

    @router.patch("/projects/{project_id}/issues/{issue_id}")
    async def patch_issue(
        project_id: str,
        issue_id: str,
        body: models.IssuePatch,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            issue, wake = rules.patch_issue(
                board,
                project_id,
                issue_id,
                body,
                time.time(),
            )
            view = rules.issue_view(
                rules.find_project(board, project_id), issue
            )
        if wake:
            dispatcher.wake()
        return view

    @router.delete("/projects/{project_id}/issues/{issue_id}")
    async def delete_issue(
        project_id: str,
        issue_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        async with store.txn() as board:
            removed = rules.delete_issue(board, project_id, issue_id)
        await cleanup_sessions([removed])
        return {"ok": True}

    @router.post("/projects/{project_id}/issues/{issue_id}/run")
    async def run_issue(
        project_id: str,
        issue_id: str,
        language: Optional[str] = None,
        ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        return await dispatcher.run_issue(ctx, project_id, issue_id, language)

    @router.post("/projects/{project_id}/issues/{issue_id}/stop")
    async def stop_issue(
        project_id: str,
        issue_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        return await dispatcher.stop_issue(project_id, issue_id)

    @router.get("/projects/{project_id}/issues/{issue_id}/stream")
    async def stream_issue(
        project_id: str, issue_id: str
    ) -> StreamingResponse:
        rules.find_issue(store.read(), project_id, issue_id)

        async def _events() -> Any:
            # Subscribing here (not in the handler) means a response that
            # never starts streaming never registers a channel.
            replay, channel = runner.subscribe(issue_id)
            try:
                for event in replay:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if channel is not None:
                    async for chunk in channel:
                        yield chunk
            finally:
                if channel is not None:
                    runner.unsubscribe(issue_id, channel)

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/projects/{project_id}/issues/{issue_id}/result")
    async def issue_result(
        project_id: str,
        issue_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        issue = rules.find_issue(store.read(), project_id, issue_id)
        return await runner.result(dispatcher.base_ctx, dict(issue))

    # ── Approvals and runtime ────────────────────────────────────────
    @router.get("/projects/{project_id}/approvals")
    async def list_approvals(
        project_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        board = store.read()
        rules.find_project(board, project_id)
        return {
            "approvals": await approvals.list_project(
                approval_api(),
                board,
                project_id,
            ),
        }

    async def _resolve(project_id: str, request_id: str, approve: bool) -> Any:
        return await approvals.resolve(
            approval_api(),
            store.read(),
            project_id,
            request_id,
            approve,
        )

    @router.post("/projects/{project_id}/approvals/{request_id}/approve")
    async def approve_request(
        project_id: str,
        request_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        return await _resolve(project_id, request_id, True)

    @router.post("/projects/{project_id}/approvals/{request_id}/deny")
    async def deny_request(
        project_id: str,
        request_id: str,
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        return await _resolve(project_id, request_id, False)

    @router.get("/approvals/summary")
    async def approvals_summary(
        _ctx: Any = Depends(ctx_dep),
    ) -> Dict[str, Any]:
        return {
            "projects": await approvals.summary(approval_api(), store.read())
        }

    @router.get("/dispatch")
    async def dispatch_state(_ctx: Any = Depends(ctx_dep)) -> Dict[str, Any]:
        return rules.dispatch_view(store.read())

    return router
