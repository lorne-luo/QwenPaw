# -*- coding: utf-8 -*-
"""State machine, membership and project lifecycle rules (requirements §8).

Pure functions over a board dict. Mutating functions are called inside a
store transaction with the transaction's private copy; they validate
everything before changing anything, so a raised ``KanbanError`` never
leaves a half-applied change (the transaction is discarded anyway).
"""

import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import models
from .errors import KanbanError

Board = Dict[str, Any]
Project = Dict[str, Any]
Issue = Dict[str, Any]
AgentCheck = Callable[[str], Optional[bool]]

SESSION_PREFIX = "pawapp:project-kanban:issue:"

# Manual status changes accepted by PATCH (requirements §8.2). ``in_progress``
# is entered only through /run (and left only through /stop or the system).
PATCH_TRANSITIONS: Set[Tuple[str, str]] = {
    ("backlog", "todo"),
    ("review", "todo"),
    ("done", "todo"),
    ("todo", "backlog"),
    ("review", "done"),
    ("backlog", "done"),
    ("todo", "done"),
    ("done", "review"),
    ("review", "backlog"),
    ("done", "backlog"),
}
RUN_FROM = ("backlog", "todo", "review")


def session_id_for(issue_id: str) -> str:
    return SESSION_PREFIX + issue_id


# ── Lookups ──────────────────────────────────────────────────────────
def find_project(board: Board, project_id: str) -> Project:
    for project in board["projects"]:
        if project["id"] == project_id:
            return project
    raise KanbanError("PROJECT_NOT_FOUND", f"project {project_id} not found")


def find_issue(board: Board, project_id: str, issue_id: str) -> Issue:
    for issue in board["issues"]:
        if issue["id"] == issue_id and issue["project_id"] == project_id:
            return issue
    raise KanbanError("ISSUE_NOT_FOUND", f"issue {issue_id} not found")


def project_by_id(board: Board) -> Dict[str, Project]:
    return {p["id"]: p for p in board["projects"]}


def member_ids(project: Project) -> List[str]:
    return [m["agent_id"] for m in project.get("members", [])]


def is_member(project: Project, agent_id: str) -> bool:
    return bool(agent_id) and agent_id in member_ids(project)


def issue_key(project: Optional[Project], issue: Issue) -> str:
    prefix = project["key"] if project else "?"
    return f"{prefix}-{issue['number']}"


def require_active(project: Project) -> None:
    if project.get("archived"):
        raise KanbanError(
            "PROJECT_ARCHIVED",
            f"project {project['key']} is archived",
        )


def project_issues(board: Board, project_id: str) -> List[Issue]:
    return [i for i in board["issues"] if i["project_id"] == project_id]


# ── Views (new dicts; never expose cache objects for mutation) ───────
def counts(board: Board, project_id: str) -> Dict[str, int]:
    result = {status: 0 for status in models.STATUSES}
    for issue in project_issues(board, project_id):
        result[issue["status"]] = result.get(issue["status"], 0) + 1
    return result


def project_view(board: Board, project: Project) -> Dict[str, Any]:
    view = dict(project)
    view["members"] = [dict(m) for m in project.get("members", [])]
    view["counts"] = counts(board, project["id"])
    return view


def issue_view(project: Optional[Project], issue: Issue) -> Dict[str, Any]:
    view = dict(issue)
    if issue.get("last_run") is not None:
        view["last_run"] = dict(issue["last_run"])
    view["key"] = issue_key(project, issue)
    return view


# ── Project keys ─────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _key_base(name: str) -> str:
    words = _WORD_RE.findall(name)
    if len(words) >= 2:
        return "".join(w[0] for w in words[:4]).upper()
    if words:
        # A single word gives one initial, which is too short for the key
        # pattern; use the word's first letters instead.
        return words[0][:3].upper()
    return ""


def derive_key(name: str, taken: Iterable[str]) -> str:
    """Derive a unique key from *name* (requirements §6.1)."""
    used = set(taken)
    base = _key_base(name)
    if not models.KEY_RE.match(base):
        number = 1
        while f"P{number}" in used:
            number += 1
        return f"P{number}"
    if base not in used:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}{suffix}"
        if candidate not in used:
            return candidate
    raise KanbanError("PROJECT_KEY_TAKEN", f"no free key for {base}")


# ── Projects ─────────────────────────────────────────────────────────
def _check_agents(agent_ids: Iterable[str], agent_exists: AgentCheck) -> None:
    for agent_id in agent_ids:
        if agent_exists(agent_id) is False:
            raise KanbanError("AGENT_NOT_FOUND", f"agent {agent_id} not found")


def create_project(
    board: Board,
    body: models.ProjectCreate,
    now: float,
    agent_exists: AgentCheck,
) -> Project:
    name = models.clean_project_name(body.name)
    description = models.clean_project_description(body.description)
    members = models.clean_agent_ids(body.members)
    taken = {p["key"] for p in board["projects"]}
    if body.key is not None and body.key.strip():
        key = models.clean_key(body.key)
        if key in taken:
            raise KanbanError("PROJECT_KEY_TAKEN", f"key {key} is taken")
    else:
        key = derive_key(name, taken)
    _check_agents(members, agent_exists)
    project = {
        "id": str(uuid.uuid4()),
        "key": key,
        "name": name,
        "description": description,
        "members": [{"agent_id": a, "added_at": now} for a in members],
        "archived": False,
        "archived_at": None,
        "next_number": 1,
        "created_at": now,
        "updated_at": now,
    }
    board["projects"].append(project)
    return project


def patch_project(
    board: Board,
    project_id: str,
    body: models.ProjectPatch,
    now: float,
) -> Project:
    project = find_project(board, project_id)
    require_active(project)
    name = (
        models.clean_project_name(body.name) if body.name is not None else None
    )
    description = (
        models.clean_project_description(body.description)
        if body.description is not None
        else None
    )
    if name is not None:
        project["name"] = name
    if description is not None:
        project["description"] = description
    if name is not None or description is not None:
        project["updated_at"] = now
    return project


def archive_project(board: Board, project_id: str, now: float) -> Project:
    project = find_project(board, project_id)
    if project.get("archived"):
        return project
    if any(
        i["status"] == "in_progress" for i in project_issues(board, project_id)
    ):
        raise KanbanError(
            "PROJECT_HAS_RUNNING",
            f"project {project['key']} has a running issue",
        )
    project["archived"] = True
    project["archived_at"] = now
    project["updated_at"] = now
    return project


def unarchive_project(
    board: Board,
    project_id: str,
    now: float,
) -> Tuple[Project, bool]:
    """Return ``(project, changed)``; queued issues resume their order."""
    project = find_project(board, project_id)
    if not project.get("archived"):
        return project, False
    project["archived"] = False
    project["archived_at"] = None
    project["updated_at"] = now
    return project, True


def delete_project(board: Board, project_id: str) -> List[Issue]:
    """Remove an archived project and its issues; return removed issues."""
    project = find_project(board, project_id)
    if not project.get("archived"):
        raise KanbanError(
            "PROJECT_NOT_ARCHIVED",
            f"project {project['key']} must be archived before delete",
        )
    removed = project_issues(board, project_id)
    board["projects"] = [p for p in board["projects"] if p["id"] != project_id]
    board["issues"] = [
        i for i in board["issues"] if i["project_id"] != project_id
    ]
    return removed


# ── Members ──────────────────────────────────────────────────────────
def add_members(
    board: Board,
    project_id: str,
    agent_ids: Any,
    now: float,
    agent_exists: AgentCheck,
) -> Project:
    project = find_project(board, project_id)
    require_active(project)
    cleaned = models.clean_agent_ids(agent_ids)
    if not cleaned:
        raise KanbanError("VALIDATION_ERROR", "agent_ids: must not be empty")
    current = set(member_ids(project))
    new_ids = [a for a in cleaned if a not in current]
    _check_agents(new_ids, agent_exists)
    for agent_id in new_ids:
        project["members"].append({"agent_id": agent_id, "added_at": now})
    if new_ids:
        project["updated_at"] = now
    return project


def remove_member(
    board: Board,
    project_id: str,
    agent_id: str,
    now: float,
) -> Tuple[Project, int, int]:
    """Return ``(project, unassigned, dequeued)`` (requirements R-MEM-3/4)."""
    project = find_project(board, project_id)
    require_active(project)
    if not is_member(project, agent_id):
        raise KanbanError("MEMBER_NOT_FOUND", f"{agent_id} is not a member")
    issues = [
        i
        for i in project_issues(board, project_id)
        if i.get("assignee") == agent_id
    ]
    if any(i["status"] == "in_progress" for i in issues):
        raise KanbanError(
            "MEMBER_BUSY",
            f"{agent_id} is running an issue in {project['key']}",
        )
    unassigned = dequeued = 0
    for issue in issues:
        if issue["status"] == "backlog":
            issue["assignee"] = ""
            issue["updated_at"] = now
            unassigned += 1
        elif issue["status"] == "todo":
            issue["assignee"] = ""
            issue["status"] = "backlog"
            issue["queued_at"] = None
            issue["updated_at"] = now
            dequeued += 1
    project["members"] = [
        m for m in project["members"] if m["agent_id"] != agent_id
    ]
    project["updated_at"] = now
    return project, unassigned, dequeued


# ── Issues ───────────────────────────────────────────────────────────
def _require_member_assignee(project: Project, assignee: str) -> None:
    if not assignee:
        raise KanbanError("ASSIGNEE_REQUIRED", "an assignee is required")
    if not is_member(project, assignee):
        raise KanbanError(
            "ASSIGNEE_NOT_MEMBER",
            f"{assignee} is not a member of {project['key']}",
        )


def create_issue(
    board: Board,
    project_id: str,
    body: models.IssueCreate,
    now: float,
) -> Issue:
    project = find_project(board, project_id)
    require_active(project)
    title = models.clean_issue_title(body.title)
    description = models.clean_issue_description(body.description)
    language = models.clean_language(body.language)
    status = models.clean_status(body.status, models.CREATE_STATUSES)
    assignee = (body.assignee or "").strip()
    if status == "todo":
        _require_member_assignee(project, assignee)
    elif assignee and not is_member(project, assignee):
        raise KanbanError(
            "ASSIGNEE_NOT_MEMBER",
            f"{assignee} is not a member of {project['key']}",
        )
    number = project["next_number"]
    project["next_number"] = number + 1
    project["updated_at"] = now
    issue = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "number": number,
        "title": title,
        "description": description,
        "status": status,
        "assignee": assignee,
        "language": language,
        "queued_at": now if status == "todo" else None,
        "last_run": None,
        "created_at": now,
        "updated_at": now,
    }
    board["issues"].append(issue)
    return issue


def check_patch_transition(
    project: Project,
    current: str,
    target: str,
    assignee: str,
) -> None:
    """Raise unless PATCH may move an issue from *current* to *target*."""
    if current == "in_progress":
        raise KanbanError("ISSUE_RUNNING", "issue is running; stop it first")
    if (current, target) not in PATCH_TRANSITIONS:
        raise KanbanError(
            "INVALID_TRANSITION",
            f"cannot move from {current} to {target}",
        )
    if target == "todo":
        require_active(project)
        _require_member_assignee(project, assignee)


def patch_issue(
    board: Board,
    project_id: str,
    issue_id: str,
    body: models.IssuePatch,
    now: float,
) -> Tuple[Issue, bool]:
    """Apply a PATCH; return ``(issue, wake_dispatcher)``."""
    project = find_project(board, project_id)
    require_active(project)
    issue = find_issue(board, project_id, issue_id)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return issue, False
    if issue["status"] == "in_progress":
        raise KanbanError("ISSUE_RUNNING", "issue is running; stop it first")

    title = (
        models.clean_issue_title(body.title)
        if body.title is not None
        else None
    )
    description = (
        models.clean_issue_description(body.description)
        if body.description is not None
        else None
    )
    language = (
        models.clean_language(body.language)
        if body.language is not None
        else None
    )

    current = issue["status"]
    old_assignee = issue.get("assignee") or ""
    new_assignee = old_assignee
    if body.assignee is not None and body.assignee.strip() != old_assignee:
        if current not in ("backlog", "todo"):
            raise KanbanError(
                "ASSIGNEE_LOCKED",
                "assignee can change only in backlog or todo",
            )
        new_assignee = body.assignee.strip()
        if new_assignee and not is_member(project, new_assignee):
            raise KanbanError(
                "ASSIGNEE_NOT_MEMBER",
                f"{new_assignee} is not a member of {project['key']}",
            )
    assignee_changed = new_assignee != old_assignee

    target = current
    if body.status is not None:
        target = models.clean_status(body.status)
        if target != current:
            check_patch_transition(project, current, target, new_assignee)
    if current == "todo" and target == "todo" and assignee_changed:
        if not new_assignee:
            target = "backlog"  # R-SM-3: unassigning a queued issue
    if (
        target == "backlog"
        and new_assignee
        and not is_member(
            project,
            new_assignee,
        )
    ):
        new_assignee = ""  # I5: reopened work leaves a former member

    # Validation done; apply.
    if title is not None:
        issue["title"] = title
    if description is not None:
        issue["description"] = description
    if language is not None:
        issue["language"] = language
    issue["assignee"] = new_assignee
    wake = False
    if target != current:
        issue["status"] = target
        if target == "todo":
            issue["queued_at"] = now
            wake = True
        else:
            issue["queued_at"] = None
    elif target == "todo" and assignee_changed:
        issue["queued_at"] = now  # end of the new agent's queue
        wake = True
    issue["updated_at"] = now
    return issue, wake


def delete_issue(board: Board, project_id: str, issue_id: str) -> Issue:
    project = find_project(board, project_id)
    require_active(project)
    issue = find_issue(board, project_id, issue_id)
    if issue["status"] == "in_progress":
        raise KanbanError("ISSUE_RUNNING", "issue is running; stop it first")
    board["issues"] = [i for i in board["issues"] if i["id"] != issue_id]
    return issue


# ── Runs ─────────────────────────────────────────────────────────────
def running_issue_of(board: Board, agent_id: str) -> Optional[Issue]:
    for issue in board["issues"]:
        if issue["status"] == "in_progress" and issue["assignee"] == agent_id:
            return issue
    return None


def start_run(issue: Issue, now: float) -> Dict[str, Any]:
    """Flip *issue* to ``in_progress``; return launch info for the runner."""
    previous = issue.get("last_run") or {}
    agent_id = issue["assignee"]
    issue["status"] = "in_progress"
    issue["queued_at"] = None
    issue["last_run"] = {
        "agent_id": agent_id,
        "session_id": session_id_for(issue["id"]),
        "started_at": now,
        "finished_at": None,
        "outcome": None,
        "error": None,
        "result_excerpt": None,
    }
    issue["updated_at"] = now
    return {
        "issue_id": issue["id"],
        "project_id": issue["project_id"],
        "agent_id": agent_id,
        "session_id": issue["last_run"]["session_id"],
        "started_at": now,
        "previous_agent_id": previous.get("agent_id") or "",
    }


def plan_run(
    board: Board,
    project_id: str,
    issue_id: str,
    language: Optional[str],
    now: float,
    *,
    agent_busy: Callable[[str], bool],
) -> Tuple[Issue, Optional[Dict[str, Any]]]:
    """``/run``: start now (launch info) or queue behind a busy agent.

    *agent_busy* is supplied by the dispatcher and must also cover runs
    that exist only in process memory (live or still unwinding after a
    stop), which the board cannot see.
    """
    project = find_project(board, project_id)
    require_active(project)
    issue = find_issue(board, project_id, issue_id)
    if issue["status"] == "in_progress":
        raise KanbanError("ISSUE_RUNNING", "issue is already running")
    if issue["status"] not in RUN_FROM:
        raise KanbanError(
            "INVALID_TRANSITION",
            f"cannot run an issue in {issue['status']}",
        )
    lang = models.clean_language(language) if language is not None else None
    _require_member_assignee(project, issue.get("assignee") or "")
    if lang is not None:
        issue["language"] = lang
    if agent_busy(issue["assignee"]):
        if issue["status"] != "todo" or issue.get("queued_at") is None:
            issue["queued_at"] = now
        issue["status"] = "todo"
        issue["updated_at"] = now
        return issue, None
    return issue, start_run(issue, now)


def finish_run(
    board: Board,
    issue_id: str,
    started_at: float,
    outcome: str,
    error: Optional[str],
    excerpt: Optional[str],
    now: float,
) -> Optional[Issue]:
    """Record a run outcome; ``None`` if the run is no longer current."""
    for issue in board["issues"]:
        if issue["id"] != issue_id:
            continue
        run = issue.get("last_run") or {}
        if issue["status"] != "in_progress" or run.get("started_at") != (
            started_at
        ):
            return None
        run["finished_at"] = now
        run["outcome"] = outcome
        if outcome == "succeeded":
            issue["status"] = "review"
            run["error"] = None
            run["result_excerpt"] = models.truncate(
                excerpt,
                models.RESULT_EXCERPT_MAX,
            )
        else:
            issue["status"] = "backlog"
            run["error"] = error or "Run failed"
        issue["updated_at"] = now
        return issue
    return None


def stop_run(
    board: Board,
    project_id: str,
    issue_id: str,
    now: float,
) -> Tuple[Issue, bool]:
    """``/stop``: return ``(issue, was_running)``; a no-op otherwise."""
    find_project(board, project_id)
    issue = find_issue(board, project_id, issue_id)
    if issue["status"] != "in_progress":
        return issue, False
    run = issue.get("last_run") or {}
    run["finished_at"] = now
    run["outcome"] = "stopped"
    issue["last_run"] = run
    issue["status"] = "backlog"
    issue["updated_at"] = now
    return issue, True


def recover_orphans(board: Board, live_ids: Set[str], now: float) -> int:
    """R-DSP-6: re-queue ``in_progress`` issues that have no live task.

    The issue goes to the head of its agent's queue: ``queued_at`` is its
    start time, or just before the agent's oldest queued issue when that
    one was queued earlier (the agent was busy until this run started).
    """
    recovered = 0
    for issue in board["issues"]:
        if issue["status"] != "in_progress" or issue["id"] in live_ids:
            continue
        run = issue.get("last_run") or {}
        run["finished_at"] = now
        run["outcome"] = "interrupted"
        issue["last_run"] = run
        issue["status"] = "todo"
        issue["queued_at"] = min(
            [run.get("started_at") or now]
            + [
                other["queued_at"] - 0.001
                for other in board["issues"]
                if other["status"] == "todo"
                and other is not issue
                and other.get("assignee") == issue["assignee"]
                and other.get("queued_at") is not None
            ],
        )
        issue["updated_at"] = now
        recovered += 1
    return recovered


# ── Queues and dispatch state ────────────────────────────────────────
def _queue_order(issue: Issue) -> Tuple[float, float, str]:
    return (
        issue.get("queued_at") or 0.0,
        issue.get("created_at") or 0.0,
        issue["id"],
    )


def agent_queues(board: Board) -> Dict[str, List[Issue]]:
    """R-DSP-2: per-agent FIFO of eligible ``todo`` issues, all projects."""
    projects = project_by_id(board)
    queues: Dict[str, List[Issue]] = {}
    for issue in board["issues"]:
        if issue["status"] != "todo" or not issue.get("assignee"):
            continue
        project = projects.get(issue["project_id"])
        if project is None or project.get("archived"):
            continue
        if not is_member(project, issue["assignee"]):
            continue
        queues.setdefault(issue["assignee"], []).append(issue)
    for queue in queues.values():
        queue.sort(key=_queue_order)
    return queues


def queue_position(board: Board, issue: Issue) -> Optional[int]:
    queue = agent_queues(board).get(issue.get("assignee") or "", [])
    for index, queued in enumerate(queue):
        if queued["id"] == issue["id"]:
            return index + 1
    return None


def issue_ref(projects: Dict[str, Project], issue: Issue) -> Dict[str, Any]:
    project = projects.get(issue["project_id"])
    return {
        "project_id": issue["project_id"],
        "project_key": project["key"] if project else "",
        "project_name": project["name"] if project else "",
        "issue_id": issue["id"],
        "key": issue_key(project, issue),
        "title": issue["title"],
    }


def dispatch_view(board: Board) -> Dict[str, Any]:
    """``GET /dispatch``: running issue and queue per agent."""
    projects = project_by_id(board)
    agents: Dict[str, Dict[str, Any]] = {}
    for issue in board["issues"]:
        if issue["status"] == "in_progress" and issue.get("assignee"):
            entry = agents.setdefault(
                issue["assignee"],
                {"running": None, "queue": []},
            )
            entry["running"] = issue_ref(projects, issue)
    for agent_id, queue in agent_queues(board).items():
        entry = agents.setdefault(agent_id, {"running": None, "queue": []})
        entry["queue"] = [issue_ref(projects, i) for i in queue]
    return {"agents": agents}
