# -*- coding: utf-8 -*-
"""Tool-approval access scoped to a project (requirements §10.3).

Only the host's public ``ApprovalService`` API is used
(``list_pending_by_session`` / ``get_pending_by_root_session`` /
``get_request`` / ``resolve_request`` /
``cancel_all_pending_by_root_session``),
so R-APR-6's private ``_pending`` access is not needed. ``load_api`` is the
single place that imports the host modules.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import get_logger, rules
from .errors import KanbanError

logger = get_logger(__name__)


@dataclass
class ApprovalApi:
    service: Any
    display_fields: Callable[[Any], Dict[str, Any]]
    approved: Any
    denied: Any


def load_api() -> Optional[ApprovalApi]:
    """Import the host approval modules; ``None`` when unavailable."""
    try:
        from qwenpaw.app.approvals import get_approval_service
        from qwenpaw.app.approvals.display import approval_display_fields
        from qwenpaw.security.tool_guard.approval import ApprovalDecision
    except ImportError:
        return None
    return ApprovalApi(
        service=get_approval_service(),
        display_fields=approval_display_fields,
        approved=ApprovalDecision.APPROVED,
        denied=ApprovalDecision.DENIED,
    )


def _running_sessions(
    board: Dict[str, Any], project_id: str
) -> Dict[str, str]:
    """Map ``session_id -> issue_id`` for running issues of the project."""
    sessions: Dict[str, str] = {}
    for issue in rules.project_issues(board, project_id):
        run = issue.get("last_run") or {}
        if issue["status"] == "in_progress" and run.get("session_id"):
            sessions[run["session_id"]] = issue["id"]
    return sessions


async def _pending_for_session(api: ApprovalApi, session_id: str) -> List[Any]:
    """Pending requests whose session or root session is *session_id*."""
    found: Dict[str, Any] = {}
    for pending in await api.service.list_pending_by_session(session_id):
        found[pending.request_id] = pending
    for pending in await api.service.get_pending_by_root_session(session_id):
        found[pending.request_id] = pending
    return sorted(
        (p for p in found.values() if p.status == "pending"),
        key=lambda p: p.created_at,
    )


def serialize(api: ApprovalApi, pending: Any) -> Dict[str, Any]:
    return {
        "request_id": pending.request_id,
        "session_id": pending.session_id,
        "root_session_id": pending.root_session_id,
        "tool_name": pending.tool_name,
        "agent_id": pending.agent_id,
        "severity": pending.severity,
        **api.display_fields(pending),
        "created_at": pending.created_at,
    }


async def list_project(
    api: Optional[ApprovalApi],
    board: Dict[str, Any],
    project_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """R-APR-1: ``{issue_id: [approval, ...]}`` for running issues."""
    if api is None:
        return {}
    result: Dict[str, List[Dict[str, Any]]] = {}
    for session_id, issue_id in _running_sessions(board, project_id).items():
        pending = await _pending_for_session(api, session_id)
        if pending:
            result[issue_id] = [serialize(api, p) for p in pending]
    return result


async def summary(
    api: Optional[ApprovalApi],
    board: Dict[str, Any],
) -> Dict[str, int]:
    """R-APR-5: pending approval count per project."""
    if api is None:
        return {}
    counts: Dict[str, int] = {}
    for project in board["projects"]:
        total = 0
        for session_id in _running_sessions(board, project["id"]):
            total += len(await _pending_for_session(api, session_id))
        if total:
            counts[project["id"]] = total
    return counts


async def resolve(
    api: Optional[ApprovalApi],
    board: Dict[str, Any],
    project_id: str,
    request_id: str,
    approve: bool,
) -> Dict[str, Any]:
    """R-APR-2/3: resolve only requests owned by the project's runs."""
    if api is None:
        raise KanbanError("APPROVAL_UNAVAILABLE", "approval service missing")
    rules.find_project(board, project_id)
    pending = await api.service.get_request(request_id)
    if pending is None or pending.status != "pending":
        raise KanbanError("APPROVAL_NOT_FOUND", f"request {request_id}")
    sessions = _running_sessions(board, project_id)
    if (
        pending.session_id not in sessions
        and pending.root_session_id not in sessions
    ):
        raise KanbanError(
            "APPROVAL_NOT_FOUND",
            f"request {request_id} does not belong to this project",
        )
    decision = api.approved if approve else api.denied
    try:
        resolved = await api.service.resolve_request(request_id, decision)
    except PermissionError as exc:  # exact-requester approvals
        raise KanbanError(
            "APPROVAL_NOT_FOUND",
            f"request {request_id} cannot be resolved from the board",
        ) from exc
    if resolved is None:  # expired (resolved as TIMEOUT) or lost a race
        raise KanbanError(
            "APPROVAL_NOT_FOUND",
            f"request {request_id} expired or was already resolved",
        )
    logger.info(
        "[project-kanban] approval %s %s tool=%s project=%s",
        request_id[:8],
        "approved" if approve else "denied",
        pending.tool_name,
        project_id,
    )
    return {"ok": True, "tool_name": pending.tool_name}


async def cancel_for_session(
    api: Optional[ApprovalApi], session_id: str
) -> int:
    """Best effort on stop: deny the run's pending approvals."""
    if api is None:
        return 0
    try:
        return await api.service.cancel_all_pending_by_root_session(session_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[project-kanban] could not cancel approvals for %s",
            session_id,
            exc_info=True,
        )
        return 0
