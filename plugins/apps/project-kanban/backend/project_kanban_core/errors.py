# -*- coding: utf-8 -*-
"""Error code catalog (requirements §11.3).

``KanbanError`` subclasses FastAPI's ``HTTPException`` so domain code can
raise it directly and FastAPI renders the structured detail
``{"detail": {"code": ..., "message": ...}}`` without extra handlers.
"""

from typing import Dict

from fastapi import HTTPException

CATALOG: Dict[str, int] = {
    "VALIDATION_ERROR": 400,
    "PROJECT_NOT_FOUND": 404,
    "ISSUE_NOT_FOUND": 404,
    "MEMBER_NOT_FOUND": 404,
    "APPROVAL_NOT_FOUND": 404,
    "AGENT_NOT_FOUND": 400,
    "PROJECT_KEY_TAKEN": 409,
    "PROJECT_ARCHIVED": 409,
    "PROJECT_NOT_ARCHIVED": 409,
    "PROJECT_HAS_RUNNING": 409,
    "MEMBER_BUSY": 409,
    "ASSIGNEE_REQUIRED": 400,
    "ASSIGNEE_NOT_MEMBER": 400,
    "ASSIGNEE_LOCKED": 409,
    "INVALID_TRANSITION": 409,
    "ISSUE_RUNNING": 409,
    "APPROVAL_UNAVAILABLE": 501,
    "BOARD_UNAVAILABLE": 503,
}


class KanbanError(HTTPException):
    """A catalogued API error. ``message`` is English text for logs."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in CATALOG:
            raise ValueError(f"Unknown error code: {code}")
        self.code = code
        self.message = message or code
        super().__init__(
            status_code=CATALOG[code],
            detail={"code": code, "message": self.message},
        )
