# -*- coding: utf-8 -*-
"""Claude Code third-party agent integration.

``ClaudeAdapter`` is exported lazily so that importing sibling modules such as
:mod:`.environment` — which the provider registry needs before it knows whether
the backend is even selected — does not pull in the optional
``claude_agent_sdk`` dependency.
"""

from typing import Any

__all__ = ["ClaudeAdapter"]


def __getattr__(name: str) -> Any:
    if name == "ClaudeAdapter":
        from .adapter import ClaudeAdapter

        return ClaudeAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
