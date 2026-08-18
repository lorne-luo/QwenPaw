# -*- coding: utf-8 -*-
"""Cross-platform discovery for the Claude Code CLI runtime."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ClaudeBinaryResolution:
    """Resolved Claude Code CLI path and discovery source."""

    path: Path
    source: str


def bundled_cli_candidate(
    *,
    platform_name: str | None = None,
) -> Path | None:
    """Return the CLI bundled with the installed Python SDK."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or spec.origin is None:
        return None
    name = "claude.exe" if _is_windows(platform_name) else "claude"
    return Path(spec.origin).resolve().parent / "_bundled" / name


def default_install_candidates(
    user_home: Path | None = None,
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[Path, str]]:
    """Return common standalone Claude Code CLI installation paths."""
    home = user_home or Path.home()
    environment = environ if environ is not None else os.environ
    if _is_windows(platform_name):
        candidates: list[tuple[Path, str]] = []
        app_data = environment.get("APPDATA")
        local_app_data = environment.get("LOCALAPPDATA")
        if app_data:
            candidates.extend(
                [
                    (Path(app_data) / "npm" / "claude.exe", "npm"),
                    (Path(app_data) / "npm" / "claude.cmd", "npm"),
                ],
            )
        if local_app_data:
            programs = Path(local_app_data) / "Programs" / "Claude"
            candidates.append(
                (
                    programs / "claude.exe",
                    "standalone",
                ),
            )
        candidates.append(
            (home / ".claude" / "local" / "claude.exe", "standalone"),
        )
        return candidates
    return [
        (home / ".local" / "bin" / "claude", "standalone"),
        (home / ".claude" / "local" / "claude", "standalone"),
        (home / ".npm-global" / "bin" / "claude", "npm"),
        (home / ".yarn" / "bin" / "claude", "yarn"),
        (home / "node_modules" / ".bin" / "claude", "npm"),
        (Path("/usr/local/bin/claude"), "standalone"),
    ]


def resolve_claude_binary(
    binary: str | None = None,
    **kwargs,
) -> Path | None:
    """Resolve only the Claude Code CLI executable path."""
    resolution = resolve_claude_binary_info(binary, **kwargs)
    return resolution.path if resolution is not None else None


def resolve_claude_binary_info(
    binary: str | None = None,
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    sdk_candidate: Path | None = None,
    install_candidates: list[tuple[Path, str]] | None = None,
) -> ClaudeBinaryResolution | None:
    """Resolve an explicit, SDK-bundled, or standalone Claude Code CLI."""
    environment = environ if environ is not None else os.environ
    if binary:
        resolved = _resolve_configured(binary, platform_name=platform_name)
        return (
            ClaudeBinaryResolution(resolved, "configured")
            if resolved is not None
            else None
        )

    environment_binary = environment.get("CLAUDE_CODE_PATH")
    if environment_binary:
        resolved = _resolve_configured(
            environment_binary,
            platform_name=platform_name,
        )
        return (
            ClaudeBinaryResolution(resolved, "environment")
            if resolved is not None
            else None
        )

    bundled = (
        sdk_candidate
        if sdk_candidate is not None
        else bundled_cli_candidate(platform_name=platform_name)
    )
    if bundled is not None and _is_executable(
        bundled,
        platform_name=platform_name,
    ):
        return ClaudeBinaryResolution(bundled.resolve(), "python-sdk")

    on_path = shutil.which("claude")
    if on_path:
        return ClaudeBinaryResolution(Path(on_path).resolve(), "path")

    candidates = (
        install_candidates
        if install_candidates is not None
        else default_install_candidates(
            platform_name=platform_name,
            environ=environment,
        )
    )
    for path, source in candidates:
        if _is_executable(path, platform_name=platform_name):
            return ClaudeBinaryResolution(path.resolve(), source)
    return None


def _resolve_configured(
    binary: str,
    *,
    platform_name: str | None = None,
) -> Path | None:
    path = Path(binary).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        if _is_executable(path, platform_name=platform_name):
            return path.resolve()
        return None
    resolved = shutil.which(binary)
    return Path(resolved).resolve() if resolved else None


def _is_executable(
    path: Path,
    *,
    platform_name: str | None = None,
) -> bool:
    if not path.is_file():
        return False
    return _is_windows(platform_name) or os.access(path, os.X_OK)


def _is_windows(platform_name: str | None) -> bool:
    value = platform_name or sys.platform
    return value in {"win32", "cygwin"}


__all__ = [
    "ClaudeBinaryResolution",
    "bundled_cli_candidate",
    "default_install_candidates",
    "resolve_claude_binary",
    "resolve_claude_binary_info",
]
