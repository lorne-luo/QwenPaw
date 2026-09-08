# -*- coding: utf-8 -*-
"""Project QwenPaw runtime capabilities into Claude Code SDK options."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..capabilities import HarnessRuntimeCapabilities

_PLUGIN_NAME = "qwenpaw-runtime"


def cli_server_name(name: str) -> str:
    """Server name as Claude Code spells it in ``mcp__<server>__<tool>``."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def materialize_skill_plugin(
    state_dir: Path,
    capabilities: HarnessRuntimeCapabilities,
) -> Path | None:
    """Create an immutable local Claude Code plugin for effective Skills."""
    if not capabilities.skills:
        return None
    root = state_dir / "claude" / "skills"
    target = root / capabilities.fingerprint
    if target.is_dir():
        return target

    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{capabilities.fingerprint}.",
            dir=root,
        ),
    )
    try:
        manifest_dir = temporary / ".claude-plugin"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": _PLUGIN_NAME,
                    "version": "1.0.0",
                    "description": (
                        "QwenPaw-managed Skills for this Harness session"
                    ),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        skill_root = temporary / "skills"
        skill_root.mkdir()
        for skill in capabilities.skills:
            shutil.copytree(
                skill.directory,
                skill_root / skill.name,
            )
        try:
            os.replace(temporary, target)
        except OSError:
            if not target.is_dir():
                raise
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def skill_names(
    capabilities: HarnessRuntimeCapabilities,
) -> list[str]:
    """Return Claude Code's qualified names for projected Skills."""
    return [f"{_PLUGIN_NAME}:{skill.name}" for skill in capabilities.skills]


def mcp_servers(
    capabilities: HarnessRuntimeCapabilities,
) -> dict[str, Any]:
    """Convert resolved MCP servers to Claude Code SDK dictionaries."""
    result: dict[str, Any] = {}
    for server in capabilities.mcp_servers:
        if server.tools == [] or (
            server.tools is None
            and server.default_policy == "deny"
            and not any(
                effect != "deny" for effect in server.tool_policies.values()
            )
        ):
            continue
        if server.transport == "stdio":
            config: dict[str, Any] = {
                "type": "stdio",
                "command": server.command,
                "args": list(server.args),
                "env": server.revealed_env(),
            }
        else:
            config = {
                "type": (
                    "http" if server.transport == "streamable_http" else "sse"
                ),
                "url": server.url,
                "headers": server.revealed_headers(),
            }
        result[server.name] = config
    return result


def tool_policies(
    capabilities: HarnessRuntimeCapabilities,
) -> tuple[list[str], list[str]]:
    """Split per-tool MCP policies into Claude Code allow and deny lists.

    Claude Code has no per-server permission policy, so QwenPaw expresses tool
    policy through the CLI's ``mcp__<server>__<tool>`` rule names. An ``ask``
    policy is deliberately absent from both lists; the PreToolUse gate handles
    approval and enforces the allowlist independently of CLI permission mode.
    """
    allowed: list[str] = []
    disallowed: list[str] = []
    for server in capabilities.mcp_servers:
        if server.tools == [] or (
            server.tools is None
            and server.default_policy == "deny"
            and not any(
                effect != "deny" for effect in server.tool_policies.values()
            )
        ):
            continue
        for name, effect in server.tool_policies.items():
            rule = f"mcp__{cli_server_name(server.name)}__{name}"
            if effect == "allow":
                allowed.append(rule)
            elif effect == "deny":
                disallowed.append(rule)
    return sorted(allowed), sorted(disallowed)


__all__ = [
    "cli_server_name",
    "materialize_skill_plugin",
    "mcp_servers",
    "skill_names",
    "tool_policies",
]
