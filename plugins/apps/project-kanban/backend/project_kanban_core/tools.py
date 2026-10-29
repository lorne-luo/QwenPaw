# -*- coding: utf-8 -*-
"""Read-only agent tools (requirements §12).

Tools read the in-memory board cache (never the write lock), return plain
JSON-serialisable dicts with English field names, and only show projects
the calling agent is a member of. Non-members get ``NOT_A_MEMBER`` whether
or not the project exists.

The caller is the agent bound to the current turn by the host's
request-setup hook, read with ``peek_current_agent_id()``. The host's
``get_current_agent_id()`` is not used: when nothing is bound it falls
back to the configured active agent, which would let a call borrow that
agent's identity. Unbound -> ``UNKNOWN_CALLER`` (R-TOOL-2).
"""

import re
from typing import Any, Callable, Dict, List, Optional

from . import models, rules
from .errors import KanbanError
from .store import Store

ISSUE_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,5})-(\d+)$")


def current_caller() -> str:
    try:
        from qwenpaw.app.agent_context import peek_current_agent_id

        return peek_current_agent_id() or ""  # "" when unbound
    except Exception:  # noqa: BLE001 - host API unavailable
        return ""


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"error": code, "message": message}


class KanbanTools:
    """Tool implementations; ``build_tools`` exposes them to the host."""

    def __init__(
        self,
        store: Store,
        caller: Optional[Callable[[], str]] = None,
    ) -> None:
        self.store = store
        self.caller = caller

    def _context(self) -> Any:
        """Return ``(caller, board)`` or an error dict."""
        caller = (self.caller or current_caller)()
        if not caller:
            return _error("UNKNOWN_CALLER", "The calling agent is unknown.")
        try:
            return caller, self.store.read()
        except KanbanError as exc:
            return _error(exc.code, exc.message)

    @staticmethod
    def _not_member(ref: str) -> Dict[str, Any]:
        return _error(
            "NOT_A_MEMBER",
            f"You are not a member of a project matching {ref!r}.",
        )

    @staticmethod
    def _find_project(
        board: Dict[str, Any], ref: str
    ) -> Optional[Dict[str, Any]]:
        ref = (ref or "").strip()
        for project in board["projects"]:
            if project["id"] == ref or project["key"] == ref.upper():
                return project
        return None

    @staticmethod
    def _find_issue(
        board: Dict[str, Any], ref: str
    ) -> Optional[Dict[str, Any]]:
        ref = (ref or "").strip()
        match = ISSUE_KEY_RE.match(ref)
        projects = rules.project_by_id(board)
        for issue in board["issues"]:
            if issue["id"] == ref:
                return issue
            if match:
                project = projects.get(issue["project_id"])
                if (
                    project
                    and project["key"] == match.group(1).upper()
                    and issue["number"] == int(match.group(2))
                ):
                    return issue
        return None

    @staticmethod
    def _check_status(status: str) -> Optional[Dict[str, Any]]:
        if status and status not in models.STATUSES:
            return _error(
                "INVALID_ARGUMENT",
                "status must be one of " + ", ".join(models.STATUSES),
            )
        return None

    @staticmethod
    def _summary(
        project: Dict[str, Any],
        issue: Dict[str, Any],
        excerpt_limit: Optional[int],
    ) -> Dict[str, Any]:
        run = issue.get("last_run") or {}
        excerpt = run.get("result_excerpt")
        if excerpt_limit is not None:
            excerpt = models.truncate(excerpt, excerpt_limit)
        return {
            "id": issue["id"],
            "key": rules.issue_key(project, issue),
            "title": issue["title"],
            "status": issue["status"],
            "assignee": issue.get("assignee") or "",
            "updated_at": issue.get("updated_at"),
            "result_excerpt": excerpt,
        }

    async def list_my_projects(
        self, include_archived: bool = False
    ) -> Dict[str, Any]:
        context = self._context()
        if isinstance(context, dict):
            return context
        caller, board = context
        projects = []
        for project in board["projects"]:
            if not rules.is_member(project, caller):
                continue
            if project.get("archived") and not include_archived:
                continue
            mine = [
                {
                    "id": i["id"],
                    "key": rules.issue_key(project, i),
                    "title": i["title"],
                    "status": i["status"],
                }
                for i in rules.project_issues(board, project["id"])
                if i.get("assignee") == caller and i["status"] != "done"
            ]
            projects.append(
                {
                    "id": project["id"],
                    "key": project["key"],
                    "name": project["name"],
                    "description": models.truncate(
                        project.get("description") or "",
                        models.TOOL_DESCRIPTION_MAX,
                    ),
                    "archived": bool(project.get("archived")),
                    "members": rules.member_ids(project),
                    "counts": rules.counts(board, project["id"]),
                    "my_open_issues": mine,
                },
            )
        return {"agent_id": caller, "projects": projects}

    async def list_project_issues(
        self,
        project: str,
        status: str = "",
        assignee: str = "",
        limit: int = models.TOOL_LIMIT_DEFAULT,
    ) -> Dict[str, Any]:
        context = self._context()
        if isinstance(context, dict):
            return context
        caller, board = context
        found = self._find_project(board, project)
        if found is None or not rules.is_member(found, caller):
            return self._not_member(project)
        invalid = self._check_status(status)
        if invalid:
            return invalid
        try:
            limit = max(1, min(int(limit), models.TOOL_LIMIT_MAX))
        except (TypeError, ValueError):
            return _error("INVALID_ARGUMENT", "limit must be an integer")
        issues = [
            i
            for i in rules.project_issues(board, found["id"])
            if (not status or i["status"] == status)
            and (not assignee or i.get("assignee") == assignee)
        ]
        issues.sort(key=lambda i: i.get("updated_at") or 0, reverse=True)
        return {
            "project": {
                "id": found["id"],
                "key": found["key"],
                "name": found["name"],
            },
            "total": len(issues),
            "issues": [
                self._summary(found, i, models.TOOL_EXCERPT_MAX)
                for i in issues[:limit]
            ],
        }

    async def get_issue(self, issue: str) -> Dict[str, Any]:
        context = self._context()
        if isinstance(context, dict):
            return context
        caller, board = context
        found = self._find_issue(board, issue)
        project = (
            rules.project_by_id(board).get(found["project_id"])
            if found
            else None
        )
        if (
            found is None
            or project is None
            or not rules.is_member(project, caller)
        ):
            return self._not_member(issue)
        result = self._summary(project, found, None)
        result.update(
            {
                "project_id": project["id"],
                "project_key": project["key"],
                "description": found.get("description") or "",
                "last_run": (
                    dict(found["last_run"]) if found.get("last_run") else None
                ),
            },
        )
        return result

    async def list_my_issues(self, status: str = "") -> Dict[str, Any]:
        context = self._context()
        if isinstance(context, dict):
            return context
        caller, board = context
        invalid = self._check_status(status)
        if invalid:
            return invalid
        result: List[Dict[str, Any]] = []
        for project in board["projects"]:
            if not rules.is_member(project, caller):
                continue
            for issue in rules.project_issues(board, project["id"]):
                if issue.get("assignee") != caller:
                    continue
                if status and issue["status"] != status:
                    continue
                summary = self._summary(
                    project, issue, models.TOOL_EXCERPT_MAX
                )
                summary["project_key"] = project["key"]
                summary["project_archived"] = bool(project.get("archived"))
                result.append(summary)
        result.sort(key=lambda i: i.get("updated_at") or 0, reverse=True)
        return {"agent_id": caller, "issues": result}


def build_tools(store: Store) -> List[Dict[str, Any]]:
    """Return tool specs ``{name, func, description, icon}`` for PawApp."""
    tools = KanbanTools(store)

    async def project_kanban_list_my_projects(
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        """List the Project Kanban projects you are a member of.

        Use this to find out which projects you work on, what each project
        is about, and which of your issues are still open.

        Args:
            include_archived (bool): Also list archived (read-only)
                projects. Defaults to False.

        Returns:
            ``{"agent_id", "projects": [{"id", "key", "name", "description",
            "archived", "members", "counts", "my_open_issues"}]}``.
        """
        return await tools.list_my_projects(include_archived)

    async def project_kanban_list_project_issues(
        project: str,
        status: str = "",
        assignee: str = "",
        limit: int = models.TOOL_LIMIT_DEFAULT,
    ) -> Dict[str, Any]:
        """List the issues of one Project Kanban project you belong to.

        Use this to see what your teammates are working on and to read
        their result excerpts before or while working on your own issue.

        Args:
            project (str): Project id or project key (for example "WEB").
            status (str): Optional filter: backlog, todo, in_progress,
                review or done.
            assignee (str): Optional filter by assignee agent id.
            limit (int): Maximum issues to return (default 50, max 200).

        Returns:
            ``{"project", "total", "issues": [{"id", "key", "title",
            "status", "assignee", "updated_at", "result_excerpt"}]}``,
            newest first, or ``{"error": "NOT_A_MEMBER", ...}``.
        """
        return await tools.list_project_issues(
            project, status, assignee, limit
        )

    async def project_kanban_get_issue(issue: str) -> Dict[str, Any]:
        """Get one Project Kanban issue in full.

        Use this when a teammate's issue matters to your work and the
        excerpt from the issue list is not enough.

        Args:
            issue (str): Issue id or issue key such as "WEB-12".

        Returns:
            The issue with full description, status, assignee and last run
            (including the full result excerpt), or
            ``{"error": "NOT_A_MEMBER", ...}``.
        """
        return await tools.get_issue(issue)

    async def project_kanban_list_my_issues(
        status: str = "",
    ) -> Dict[str, Any]:
        """List the Project Kanban issues assigned to you, all projects.

        Args:
            status (str): Optional filter: backlog, todo, in_progress,
                review or done.

        Returns:
            ``{"agent_id", "issues": [{"id", "key", "project_key", "title",
            "status", "updated_at", "result_excerpt", ...}]}``, newest first.
        """
        return await tools.list_my_issues(status)

    return [
        {
            "name": "project_kanban_list_my_projects",
            "func": project_kanban_list_my_projects,
            "description": (
                "List the Project Kanban projects you are a member of, with "
                "their brief, members, issue counts and your open issues."
            ),
            "icon": "🗂️",
        },
        {
            "name": "project_kanban_list_project_issues",
            "func": project_kanban_list_project_issues,
            "description": (
                "List the issues of a Project Kanban project you belong to "
                "(by id or key), including teammates' result excerpts."
            ),
            "icon": "📋",
        },
        {
            "name": "project_kanban_get_issue",
            "func": project_kanban_get_issue,
            "description": (
                "Get one Project Kanban issue (id or key like WEB-12) with "
                "its full description and last run result."
            ),
            "icon": "🔎",
        },
        {
            "name": "project_kanban_list_my_issues",
            "func": project_kanban_list_my_issues,
            "description": (
                "List the Project Kanban issues assigned to you across all "
                "your projects."
            ),
            "icon": "🧾",
        },
    ]
