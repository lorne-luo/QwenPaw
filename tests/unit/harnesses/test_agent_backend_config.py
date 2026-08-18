# -*- coding: utf-8 -*-
"""Tests for agent-level runtime backend configuration."""

from qwenpaw.config.config import AgentProfileConfig
from qwenpaw.harnesses.registry import get_provider


def test_agent_backend_defaults_to_qwenpaw() -> None:
    config = AgentProfileConfig(id="agent-1", name="Agent")

    assert config.backend == "qwenpaw"


def test_codex_backend_uses_the_agent_workspace() -> None:
    config = AgentProfileConfig(
        id="codex-1",
        name="Codex",
        backend="codex",
        workspace_dir="/workspaces/codex-1",
    )

    assert config.backend == "codex"
    assert config.workspace_dir == "/workspaces/codex-1"


def test_future_backend_settings_do_not_require_core_schema_changes() -> None:
    config = AgentProfileConfig(
        id="qoder-1",
        name="Qoder",
        backend="qoder",
        backend_settings={
            "model": "qoder-default",
            "provider_option": True,
        },
    )

    assert config.backend == "qoder"
    assert config.backend_settings["model"] == "qoder-default"
    assert config.backend_settings["provider_option"] is True


def test_codex_declares_console_attachment_support() -> None:
    capabilities = get_provider("codex").capabilities

    assert capabilities.attachments is True


def test_qoder_declares_console_attachment_support() -> None:
    capabilities = get_provider("qoder").capabilities

    assert capabilities.attachments is True


def test_claude_backend_accepts_environment_overrides() -> None:
    config = AgentProfileConfig(
        id="claude-1",
        name="Claude Code",
        backend="claude",
        backend_settings={
            "model": "opus",
            "env": {"ANTHROPIC_BASE_URL": "https://gateway.example.test/v1"},
        },
    )

    assert config.backend == "claude"
    assert config.coding_mode.enabled is False
    assert config.backend_settings["model"] == "opus"
    assert config.backend_settings["env"] == {
        "ANTHROPIC_BASE_URL": "https://gateway.example.test/v1",
    }


def test_claude_declares_console_attachment_support() -> None:
    capabilities = get_provider("claude").capabilities

    assert capabilities.attachments is True
    assert capabilities.model_selection is True
    assert capabilities.reasoning_effort is True


def test_claude_approval_presets_match_the_cli_permission_modes() -> None:
    presets = get_provider("claude").capabilities.approval_presets

    assert [preset.settings["permission_mode"] for preset in presets] == [
        "default",
        "acceptEdits",
        "plan",
        "auto",
        "bypassPermissions",
    ]
