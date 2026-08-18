# -*- coding: utf-8 -*-
"""Tests for resolving one Claude Code agent's effective environment."""

from __future__ import annotations

import json
from pathlib import Path

from qwenpaw.harnesses.claude.environment import (
    agent_env,
    fingerprint,
    resolve,
)


def _settings(path: Path, env: dict[str, object]) -> Path:
    path.write_text(json.dumps({"env": env}), encoding="utf-8")
    return path


def test_agent_env_reads_the_backend_settings_dictionary() -> None:
    assert agent_env({"env": {"ANTHROPIC_BASE_URL": "https://gateway"}}) == {
        "ANTHROPIC_BASE_URL": "https://gateway",
    }


def test_agent_env_coerces_values_and_drops_blank_keys() -> None:
    assert agent_env(
        {"env": {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": 1000000, "  ": "x"}},
    ) == {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000"}


def test_agent_env_is_empty_without_configuration() -> None:
    assert agent_env(None) == {}
    assert agent_env({"binary": "/usr/local/bin/claude"}) == {}
    assert agent_env({"env": "ANTHROPIC_BASE_URL=x"}) == {}


def test_agent_env_reads_the_console_editor_rows() -> None:
    assert agent_env(
        {
            "env": [
                {"name": "ANTHROPIC_BASE_URL", "value": "https://gateway"},
                {"name": " ANTHROPIC_AUTH_TOKEN ", "value": "sk-token"},
            ],
        },
    ) == {
        "ANTHROPIC_BASE_URL": "https://gateway",
        "ANTHROPIC_AUTH_TOKEN": "sk-token",
    }


def test_agent_env_drops_incomplete_editor_rows() -> None:
    assert agent_env(
        {
            "env": [
                {"name": "", "value": "orphan"},
                {"name": "ANTHROPIC_BASE_URL"},
                {"value": "no name"},
                "not a row",
                {"name": "KEPT", "value": "yes"},
            ],
        },
    ) == {"KEPT": "yes"}


def test_later_editor_rows_win_over_earlier_duplicates() -> None:
    assert agent_env(
        {
            "env": [
                {"name": "MODEL", "value": "first"},
                {"name": "MODEL", "value": "second"},
            ],
        },
    ) == {"MODEL": "second"}


def test_both_editor_shapes_fingerprint_identically() -> None:
    rows = agent_env({"env": [{"name": "A", "value": "1"}]})
    mapping = agent_env({"env": {"A": "1"}})

    assert rows == mapping
    assert fingerprint(rows) == fingerprint(mapping)


def test_agent_variables_win_over_process_and_user_settings(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path / "settings.json",
        {"ANTHROPIC_DEFAULT_OPUS_MODEL": "from-settings"},
    )

    resolved = resolve(
        {"ANTHROPIC_DEFAULT_OPUS_MODEL": "from-agent"},
        environ={"ANTHROPIC_DEFAULT_OPUS_MODEL": "from-process"},
        user_settings_path=settings,
    )

    assert resolved["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "from-agent"


def test_process_environment_wins_over_user_settings(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path / "settings.json",
        {"ANTHROPIC_BASE_URL": "from-settings"},
    )

    resolved = resolve(
        {},
        environ={"ANTHROPIC_BASE_URL": "from-process"},
        user_settings_path=settings,
    )

    assert resolved["ANTHROPIC_BASE_URL"] == "from-process"


def test_user_settings_are_visible_when_nothing_else_defines_them(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path / "settings.json",
        {"ANTHROPIC_DEFAULT_HAIKU_MODEL": "openlite"},
    )

    resolved = resolve({}, environ={}, user_settings_path=settings)

    assert resolved["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "openlite"


def test_missing_user_settings_file_is_tolerated(tmp_path: Path) -> None:
    resolved = resolve(
        {"A": "1"},
        environ={},
        user_settings_path=tmp_path / "absent.json",
    )

    assert resolved == {"A": "1"}


def test_malformed_user_settings_file_is_tolerated(tmp_path: Path) -> None:
    broken = tmp_path / "settings.json"
    broken.write_text("{not json", encoding="utf-8")

    resolved = resolve({"A": "1"}, environ={}, user_settings_path=broken)

    assert resolved == {"A": "1"}


def test_user_settings_without_an_env_block_is_tolerated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "opus"}), encoding="utf-8")

    resolved = resolve({}, environ={"B": "2"}, user_settings_path=path)

    assert resolved == {"B": "2"}


def test_fingerprint_is_order_independent_and_value_sensitive() -> None:
    first = fingerprint({"A": "1", "B": "2"})
    reordered = fingerprint({"B": "2", "A": "1"})
    changed = fingerprint({"A": "1", "B": "3"})

    assert first == reordered
    assert first != changed
    assert fingerprint(None) == fingerprint({})


def test_fingerprint_does_not_leak_values() -> None:
    digest = fingerprint({"ANTHROPIC_AUTH_TOKEN": "sk-secret"})

    assert "sk-secret" not in digest
