# -*- coding: utf-8 -*-
"""Resolve the environment one Claude Code agent effectively runs under."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping

_USER_SETTINGS = Path.home() / ".claude" / "settings.json"


def agent_env(settings: Mapping[str, object] | None) -> dict[str, str]:
    """Return the operator-supplied environment for one agent backend.

    Accepts either a plain mapping, which is what a hand-edited config file
    carries, or the console editor's ordered ``[{"name": …, "value": …}]``
    rows. Later rows win, matching what the editor shows.
    """
    raw = (settings or {}).get("env")
    if isinstance(raw, dict):
        return _pairs((str(key), value) for key, value in raw.items())
    if isinstance(raw, list):
        return _pairs(
            (str(row.get("name") or ""), row.get("value"))
            for row in raw
            if isinstance(row, dict)
        )
    return {}


def resolve(
    env: Mapping[str, str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    user_settings_path: Path | None = None,
) -> dict[str, str]:
    """Return the environment Claude Code will effectively see.

    Merged lowest priority first: the ``env`` block of the user's Claude Code
    settings file, then the QwenPaw process environment, then the agent's own
    variables. Only the agent's own variables are handed to the SDK — the CLI
    already applies the other two itself. This full merge exists so status
    probing and the model catalog can read the same effective configuration
    the CLI will run under.

    Reads a file; call it off the event loop.
    """
    resolved: dict[str, str] = dict(
        _user_settings_env(user_settings_path or _USER_SETTINGS),
    )
    resolved.update(environ if environ is not None else os.environ)
    resolved.update(env or {})
    return resolved


def fingerprint(env: Mapping[str, str] | None) -> str:
    """Return a stable digest of one agent's environment overrides.

    Values are hashed rather than stored so credentials never reach backend
    cache keys or logs.
    """
    payload = json.dumps(
        sorted((str(key), str(value)) for key, value in (env or {}).items()),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pairs(items: Iterable[tuple[str, object]]) -> dict[str, str]:
    return {
        key.strip(): str(value)
        for key, value in items
        if key.strip() and value is not None
    }


def _user_settings_env(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    block = document.get("env")
    if not isinstance(block, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in block.items()
        if value is not None
    }


__all__ = ["agent_env", "fingerprint", "resolve"]
