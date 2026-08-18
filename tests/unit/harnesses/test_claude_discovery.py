# -*- coding: utf-8 -*-
"""Tests for cross-platform Claude Code CLI discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from qwenpaw.harnesses.claude.discovery import (
    default_install_candidates,
    resolve_claude_binary_info,
)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True)
    path.touch()
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_prefers_explicit_claude_binary(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "custom" / "claude")
    bundled = _executable(tmp_path / "sdk" / "claude")

    resolution = resolve_claude_binary_info(
        str(binary),
        sdk_candidate=bundled,
        install_candidates=[],
    )

    assert resolution is not None
    assert resolution.path == binary
    assert resolution.source == "configured"


def test_reads_official_claude_environment_path(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "environment" / "claude")

    resolution = resolve_claude_binary_info(
        environ={"CLAUDE_CODE_PATH": str(binary)},
        sdk_candidate=tmp_path / "missing",
        install_candidates=[],
    )

    assert resolution is not None
    assert resolution.path == binary
    assert resolution.source == "environment"


def test_prefers_python_sdk_bundled_cli(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "sdk" / "claude")

    resolution = resolve_claude_binary_info(
        environ={},
        sdk_candidate=binary,
        install_candidates=[],
    )

    assert resolution is not None
    assert resolution.path == binary
    assert resolution.source == "python-sdk"


def test_invalid_manual_binary_does_not_fall_back(
    tmp_path: Path,
) -> None:
    bundled = _executable(tmp_path / "sdk" / "claude")

    resolution = resolve_claude_binary_info(
        str(tmp_path / "missing" / "claude"),
        sdk_candidate=bundled,
        install_candidates=[],
    )

    assert resolution is None


def test_windows_candidates_include_npm_and_standalone_paths(
    tmp_path: Path,
) -> None:
    candidates = default_install_candidates(
        tmp_path,
        platform_name="win32",
        environ={
            "APPDATA": str(tmp_path / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        },
    )

    paths = {path for path, _ in candidates}
    npm_binary = tmp_path / "AppData" / "Roaming" / "npm" / "claude.cmd"
    standalone_binary = (
        tmp_path / "AppData" / "Local" / "Programs" / "Claude" / "claude.exe"
    )
    assert npm_binary in paths
    assert standalone_binary in paths


def test_windows_executable_does_not_require_posix_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    binary = tmp_path / "claude.exe"
    binary.touch()

    resolution = resolve_claude_binary_info(
        platform_name="win32",
        environ={},
        sdk_candidate=tmp_path / "missing.exe",
        install_candidates=[(binary, "standalone")],
    )

    assert resolution is not None
    assert resolution.path == binary
