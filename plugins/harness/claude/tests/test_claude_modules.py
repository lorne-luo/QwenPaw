# -*- coding: utf-8 -*-
"""Claude Code discovery, environment, model catalog and projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.plugins.harnesses import (
    HarnessMCPServerDefinition,
    HarnessRuntimeCapabilities,
    HarnessSkillDefinition,
)
from plugins.harness.claude.discovery import (
    default_install_candidates,
    resolve_claude_binary_info,
)
from plugins.harness.claude.environment import (
    agent_env,
    fingerprint,
    resolve,
)
from plugins.harness.claude.models import catalog, from_server_info
from plugins.harness.claude.projection import (
    materialize_skill_plugin,
    mcp_servers,
    tool_policies,
)


# Discovery


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


# Environment


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


# Model catalog


_GATEWAY = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "gpt-luna-max",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES": (
        "1m,effort,max_effort,thinking"
    ),
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "openbest",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES": "1m,thinking",
}


def _by_id(models, model_id):
    return next(model for model in models if model.id == model_id)


def test_catalog_offers_every_alias_with_default_first() -> None:
    models = catalog({})

    assert [model.id for model in models] == [
        "default",
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert models[0].is_default is True
    assert sum(model.is_default for model in models) == 1


def test_plain_aliases_accept_every_cli_effort() -> None:
    opus = _by_id(catalog({}), "opus")

    assert opus.description == "Latest Opus model"
    assert list(opus.reasoning_efforts) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_default_alias_carries_no_reasoning_efforts() -> None:
    assert _by_id(catalog({}), "default").reasoning_efforts == ()


def test_gateway_model_is_named_and_keeps_the_alias_as_id() -> None:
    opus = _by_id(catalog(_GATEWAY), "opus")

    assert opus.id == "opus"
    assert opus.description == "gpt-luna-max"


def test_declared_max_effort_keeps_the_full_effort_range() -> None:
    opus = _by_id(catalog(_GATEWAY), "opus")

    assert list(opus.reasoning_efforts) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_effort_without_max_effort_drops_the_max_level() -> None:
    models = catalog(
        {
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "openlite",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES": (
                "1m,effort"
            ),
        },
    )

    assert list(_by_id(models, "haiku").reasoning_efforts) == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]


def test_capabilities_without_effort_offer_no_reasoning_efforts() -> None:
    sonnet = _by_id(catalog(_GATEWAY), "sonnet")

    assert sonnet.description == "openbest"
    assert list(sonnet.reasoning_efforts) == []


def test_unrouted_aliases_stay_plain_when_others_are_routed() -> None:
    fable = _by_id(catalog(_GATEWAY), "fable")

    assert fable.description == "Latest Fable model"
    assert list(fable.reasoning_efforts) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_blank_model_variable_falls_back_to_the_plain_label() -> None:
    opus = _by_id(catalog({"ANTHROPIC_DEFAULT_OPUS_MODEL": "   "}), "opus")

    assert opus.description == "Latest Opus model"


_SERVER_INFO = [
    {
        "value": "default",
        "resolvedModel": "claude-opus-5[1m]",
        "displayName": "Default (recommended)",
        "description": "Opus 5 with 1M context",
        "supportsEffort": True,
        "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
    },
    {
        "value": "haiku",
        "resolvedModel": "claude-haiku-4-5-20251001",
        "displayName": "Haiku",
        "supportsEffort": None,
        "supportedEffortLevels": None,
    },
]


def test_server_info_catalog_keeps_cli_ids_and_effort_support() -> None:
    models = from_server_info(_SERVER_INFO)

    assert [model.id for model in models] == ["default", "haiku"]
    assert models[0].is_default is True
    assert models[0].name == "Default (recommended)"
    assert models[0].description == (
        "claude-opus-5[1m] · Opus 5 with 1M context"
    )
    assert list(models[0].reasoning_efforts) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert list(models[1].reasoning_efforts) == []


def test_server_info_leads_with_the_resolved_gateway_model() -> None:
    models = from_server_info(
        [
            {
                "value": "opus",
                "resolvedModel": "gpt-luna-max",
                "description": "Custom Opus model",
            },
            {"value": "sonnet", "resolvedModel": "openbest"},
            {"value": "haiku", "resolvedModel": "haiku"},
        ],
    )

    assert models[0].description == "gpt-luna-max · Custom Opus model"
    assert models[0].name == "opus"
    assert models[1].description == "openbest"
    # A self-resolving id adds nothing, so it is not repeated.
    assert models[2].description == ""


def test_server_info_marks_a_default_when_the_cli_names_none() -> None:
    models = from_server_info([{"value": "sonnet"}, {"value": "haiku"}])

    assert models[0].is_default is True
    assert models[1].is_default is False


def test_server_info_drops_unusable_entries() -> None:
    models = from_server_info(
        [{"value": ""}, {"value": "   "}, "not a dict", {"value": "opus"}],
    )

    assert [model.id for model in models] == ["opus"]


def test_server_info_tolerates_a_missing_or_wrong_shaped_payload() -> None:
    assert from_server_info(None) == ()
    assert from_server_info({}) == ()
    assert from_server_info([]) == ()


# Projection


def test_skill_projection_uses_portable_copies_with_spaced_paths(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "QwenPaw Skills" / "代码审查"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n",
        encoding="utf-8",
    )
    capabilities = HarnessRuntimeCapabilities(
        skills=(HarnessSkillDefinition(name="review", directory=skill_dir),),
    )

    plugin = materialize_skill_plugin(
        tmp_path / "Harness State",
        capabilities,
    )

    assert plugin is not None
    projected = plugin / "skills" / "review" / "SKILL.md"
    assert projected.is_file()
    assert not projected.is_symlink()
    manifest = plugin / ".claude-plugin" / "plugin.json"
    assert json.loads(manifest.read_text())["name"] == "qwenpaw-runtime"
    empty = HarnessRuntimeCapabilities()
    assert materialize_skill_plugin(tmp_path, empty) is None


@pytest.mark.parametrize("default_policy", ["allow", "ask", "deny"])
def test_empty_mcp_allowlist_is_not_projected(default_policy) -> None:
    capabilities = HarnessRuntimeCapabilities(
        mcp_servers=(
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                tools=(),
                default_policy=default_policy,
            ),
        ),
    )

    assert mcp_servers(capabilities) == {}
    assert tool_policies(capabilities) == ([], [])


def test_stdio_mcp_server_reveals_its_environment() -> None:
    capabilities = HarnessRuntimeCapabilities(
        mcp_servers=(
            HarnessMCPServerDefinition(
                name="local",
                display_name="Local",
                transport="stdio",
                command="docs-mcp",
                args=("--port", "0"),
                env={"TOKEN": "secret"},
                default_policy="allow",
            ),
        ),
    )

    assert mcp_servers(capabilities) == {
        "local": {
            "type": "stdio",
            "command": "docs-mcp",
            "args": ["--port", "0"],
            "env": {"TOKEN": "secret"},
        },
    }
