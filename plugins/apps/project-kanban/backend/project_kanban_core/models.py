# -*- coding: utf-8 -*-
"""Request schemas and validation limits (requirements §6).

Pydantic models only check types (schema errors stay FastAPI's 422).
Length/enum limits are checked by the ``clean_*`` helpers so violations
return ``400 VALIDATION_ERROR`` with the field name in the message.
"""

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from .errors import KanbanError

SCHEMA_VERSION = 1

STATUSES = ("backlog", "todo", "in_progress", "review", "done")
CREATE_STATUSES = ("backlog", "todo")
LANGUAGES = ("zh", "en")

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")
PROJECT_NAME_MAX = 80
PROJECT_DESCRIPTION_MAX = 4000
ISSUE_TITLE_MAX = 200
ISSUE_DESCRIPTION_MAX = 8000
AGENT_ID_MAX = 128

RESULT_EXCERPT_MAX = 2000
TRACE_MAX = 1000
TOOL_EXCERPT_MAX = 500
TOOL_DESCRIPTION_MAX = 500
TOOL_LIMIT_DEFAULT = 50
TOOL_LIMIT_MAX = 200

OUTCOMES = ("succeeded", "failed", "stopped", "interrupted")


# ── Request schemas ──────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    key: Optional[str] = None
    description: str = ""
    members: List[str] = Field(default_factory=list)


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class MembersAdd(BaseModel):
    agent_ids: List[str]


class IssueCreate(BaseModel):
    title: str
    description: str = ""
    assignee: str = ""
    status: str = "backlog"
    language: str = "en"


class IssuePatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee: Optional[str] = None
    status: Optional[str] = None
    language: Optional[str] = None


# ── Validation helpers ───────────────────────────────────────────────
def _invalid(field: str, rule: str) -> KanbanError:
    return KanbanError("VALIDATION_ERROR", f"{field}: {rule}")


def clean_text(value: Any, field: str, min_len: int, max_len: int) -> str:
    """Trim *value* and enforce ``min_len <= len <= max_len``."""
    if not isinstance(value, str):
        raise _invalid(field, "must be a string")
    text = value.strip()
    if len(text) < min_len or len(text) > max_len:
        raise _invalid(field, f"length must be {min_len}-{max_len}")
    return text


def clean_project_name(value: Any) -> str:
    return clean_text(value, "name", 1, PROJECT_NAME_MAX)


def clean_project_description(value: Any) -> str:
    return clean_text(value, "description", 0, PROJECT_DESCRIPTION_MAX)


def clean_issue_title(value: Any) -> str:
    return clean_text(value, "title", 1, ISSUE_TITLE_MAX)


def clean_issue_description(value: Any) -> str:
    return clean_text(value, "description", 0, ISSUE_DESCRIPTION_MAX)


def clean_key(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("key", "must be a string")
    key = value.strip().upper()
    if not KEY_RE.match(key):
        raise _invalid("key", "must match ^[A-Z][A-Z0-9]{1,5}$")
    return key


def clean_language(value: Any) -> str:
    if value not in LANGUAGES:
        raise _invalid("language", "must be zh or en")
    return value


def clean_status(value: Any, allowed: tuple = STATUSES) -> str:
    if value not in allowed:
        raise _invalid("status", "must be one of " + ", ".join(allowed))
    return value


def clean_agent_id(value: Any, field: str = "agent_id") -> str:
    return clean_text(value, field, 1, AGENT_ID_MAX)


def clean_agent_ids(values: Any) -> List[str]:
    """Trim, validate and de-duplicate agent ids, keeping their order."""
    if not isinstance(values, list):
        raise _invalid("agent_ids", "must be a list")
    result: List[str] = []
    for value in values:
        agent_id = clean_agent_id(value, "agent_ids")
        if agent_id not in result:
            result.append(agent_id)
    return result


def truncate(text: Optional[str], limit: int) -> Optional[str]:
    """Cut *text* to at most *limit* characters, ending with ``…``."""
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
