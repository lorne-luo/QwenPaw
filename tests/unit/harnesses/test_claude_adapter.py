# -*- coding: utf-8 -*-
"""Tests for the Claude Code third-party agent adapter."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# pylint: disable=wrong-import-position
# flake8: noqa: E402,E501
pytest.importorskip(
    "claude_agent_sdk",
    reason="optional claude_agent_sdk (claude extra) is required",
)

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    PermissionResultAllow,
    ProcessError,
    ResultMessage,
    SessionMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)

from qwenpaw.harnesses.events import (
    HarnessAttachment,
    HarnessAttachmentKind,
    HarnessEventKind,
)
from qwenpaw.harnesses.base import (
    HarnessAdapter,
    HarnessOperationNotSupportedError,
)
from qwenpaw.harnesses.capabilities import (
    HarnessMCPServerDefinition,
    HarnessRuntimeCapabilities,
    HarnessSkillDefinition,
)
from qwenpaw.harnesses.claude.adapter import ClaudeAdapter
from qwenpaw.harnesses.claude.projection import materialize_skill_plugin
from qwenpaw.harnesses.registry import (
    adapter_config_key,
    create_adapter,
    get_provider,
)
from qwenpaw.security.tool_guard.approval import ApprovalDecision
from qwenpaw.utils.io_utils import write_json_atomic

_LOGGED_IN = json.dumps(
    {
        "loggedIn": True,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "email": "user@example.com",
        "orgName": "Example",
        "subscriptionType": "team",
    },
)
_GATEWAY_ENV = {
    "ANTHROPIC_BASE_URL": "https://gateway.example.test/v1",
    "ANTHROPIC_AUTH_TOKEN": "sk-gateway",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "gpt-luna-max",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES": (
        "1m,effort,max_effort,thinking"
    ),
}


# Ends FakeClaudeClient.receive_messages, like a CLI process that exits.
_STREAM_END = object()


class FakeClaudeClient:
    """Minimal Claude Code SDK client double.

    Each query emits ``messages`` onto one stream that ``receive_messages``
    yields across turns, like the SDK's persistent connection.
    """

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.connected = False
        self.disconnected = False
        self.interrupted = False
        self.prompts: list[tuple[Any, str]] = []
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.server_info: dict[str, Any] = {"skills": []}
        self.messages: list[Any] = [
            StreamEvent(
                uuid="stream-1",
                session_id="claude-session",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Done"},
                },
            ),
            AssistantMessage(
                content=[TextBlock(text="Done")],
                model="claude-test",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="claude-session",
            ),
        ]

    async def connect(self) -> None:
        """Mark the client connected."""
        self.connected = True

    async def disconnect(self) -> None:
        """Mark the client disconnected."""
        self.disconnected = True

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        """Capture user input."""
        if not isinstance(prompt, str):
            prompt = [message async for message in prompt]
        self.prompts.append((prompt, session_id))
        for message in self.messages:
            self.inbox.put_nowait(message)

    async def receive_messages(self):
        """Yield every message the CLI emits until the stream ends."""
        while True:
            message = await self.inbox.get()
            if message is _STREAM_END:
                return
            yield message

    async def interrupt(self) -> None:
        """Capture interruption."""
        self.interrupted = True

    async def get_server_info(self) -> dict[str, Any]:
        """Return discovered Claude Code capabilities."""
        return self.server_info


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(path.stat().st_mode | 0o111)
    return path


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
        skills=[
            HarnessSkillDefinition(name="review", directory=skill_dir),
        ],
    )

    plugin = materialize_skill_plugin(
        tmp_path / "Harness State",
        capabilities,
    )

    assert plugin is not None
    projected = plugin / "skills" / "review" / "SKILL.md"
    assert projected.is_file()
    assert not projected.is_symlink()
    assert (plugin / ".claude-plugin" / "plugin.json").is_file()


def test_loads_persisted_claude_sessions(tmp_path: Path) -> None:
    write_json_atomic(
        tmp_path / "claude_sessions.json",
        {"chat-1": "550e8400-e29b-41d4-a716-446655440000"},
    )

    adapter = ClaudeAdapter(tmp_path)

    assert adapter._sessions == {
        "chat-1": "550e8400-e29b-41d4-a716-446655440000",
    }


def test_registry_exposes_claude_capabilities(tmp_path: Path) -> None:
    provider = get_provider("claude")
    adapter = create_adapter(
        "claude",
        tmp_path,
        {"binary": "/custom/claude", "env": {"ANTHROPIC_BASE_URL": "x"}},
    )

    assert provider.coming_soon is False
    assert provider.capabilities.model_selection is True
    assert provider.capabilities.reasoning_stream is True
    assert provider.capabilities.tool_stream is True
    assert provider.capabilities.attachments is True
    assert provider.capabilities.provider_mcp_discovery is False
    assert provider.capabilities.mcp_tool_allowlist is True
    assert provider.capabilities.passthrough_commands is True
    assert [command.name for command in provider.capabilities.commands] == [
        "init",
        "compact",
        "context",
        "usage",
        "doctor",
        "mcp",
    ]
    # The CLI's own model and settings commands would desync QwenPaw's agent
    # settings, so they stay out of the menu even though passthrough allows
    # an operator to type them.
    assert not {"model", "config", "clear"} & {
        command.name for command in provider.capabilities.commands
    }
    assert isinstance(adapter, ClaudeAdapter)
    assert adapter._binary == "/custom/claude"
    assert adapter._env == {"ANTHROPIC_BASE_URL": "x"}


def test_adapter_config_key_tracks_binary_and_environment() -> None:
    base = adapter_config_key("claude", {"binary": "/custom/claude"})
    same = adapter_config_key("claude", {"binary": "/custom/claude"})
    changed_env = adapter_config_key(
        "claude",
        {"binary": "/custom/claude", "env": {"ANTHROPIC_BASE_URL": "x"}},
    )

    assert base == same
    assert base != changed_env
    assert not any("ANTHROPIC" in str(part) for part in changed_env)


@pytest.mark.asyncio
async def test_login_declines_with_actionable_guidance(
    tmp_path: Path,
) -> None:
    adapter = ClaudeAdapter(tmp_path)

    with pytest.raises(
        HarnessOperationNotSupportedError,
        match="ANTHROPIC_AUTH_TOKEN",
    ):
        await adapter.start_login()


@pytest.mark.asyncio
async def test_logout_delegates_to_the_cli(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))
    adapter._run_cli = AsyncMock(return_value="")

    await adapter.logout()

    adapter._run_cli.assert_awaited_once_with("auth", "logout", check=True)


@pytest.mark.asyncio
async def test_logout_reports_a_failing_cli(tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho 'not signed in' >&2\nexit 3\n")
    binary.chmod(0o755)
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))

    with pytest.raises(RuntimeError, match="exit 3"):
        await adapter.logout()


@pytest.mark.asyncio
async def test_status_reports_the_signed_in_account(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))
    adapter._run_cli = AsyncMock(return_value=_LOGGED_IN)

    status = await adapter.status()

    adapter._run_cli.assert_awaited_once_with("auth", "status", "--json")
    assert status.installed is True
    assert status.authenticated is True
    assert status.runtime_path == str(binary)
    assert status.account == {
        "type": "claude.ai",
        "email": "user@example.com",
        "organization": "Example",
        "plan": "team",
    }


@pytest.mark.asyncio
async def test_status_reports_a_gateway_token_account(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary), env=_GATEWAY_ENV)
    adapter._run_cli = AsyncMock(
        return_value=json.dumps(
            {
                "loggedIn": True,
                "authMethod": "oauth_token",
                "apiProvider": "firstParty",
            },
        ),
    )

    status = await adapter.status()

    assert status.authenticated is True
    assert status.account == {"type": "oauth_token"}


@pytest.mark.asyncio
async def test_status_reports_a_signed_out_installation(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))
    adapter._run_cli = AsyncMock(return_value=json.dumps({"loggedIn": False}))

    status = await adapter.status()

    assert status.installed is True
    assert status.authenticated is False
    assert status.account is None


@pytest.mark.asyncio
async def test_status_tolerates_non_json_cli_output(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))
    adapter._run_cli = AsyncMock(return_value="error: something went wrong")

    status = await adapter.status()

    assert status.installed is True
    assert status.authenticated is False


@pytest.mark.asyncio
async def test_status_reports_a_missing_installation(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(tmp_path, binary=str(tmp_path / "absent"))

    status = await adapter.status()

    assert status.installed is False
    assert status.available is True


@pytest.mark.asyncio
async def test_status_probe_runs_under_the_agent_environment(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(tmp_path, binary=str(binary), env=_GATEWAY_ENV)
    process = AsyncMock()
    process.communicate.return_value = (_LOGGED_IN.encode("utf-8"), b"")
    process.returncode = 0

    with patch(
        "qwenpaw.harnesses.claude.adapter.asyncio.create_subprocess_exec",
        return_value=process,
    ) as spawn:
        await adapter.status()

    arguments = spawn.call_args.args
    passed_env = spawn.call_args.kwargs["env"]
    assert arguments[1:] == ("auth", "status", "--json")
    assert "--settings" not in arguments
    assert passed_env["ANTHROPIC_BASE_URL"] == (
        "https://gateway.example.test/v1"
    )
    assert "PATH" in passed_env


_HANDSHAKE_MODELS = [
    {
        "value": "default",
        "resolvedModel": "gpt-luna-max[1m]",
        "displayName": "Default (recommended)",
        "description": "Opus 5 with 1M context",
        "supportsEffort": True,
        "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
    },
    {
        "value": "haiku",
        "resolvedModel": "openlite",
        "displayName": "Haiku",
        "supportsEffort": False,
    },
    {"value": "", "displayName": "Broken"},
]


@pytest.mark.asyncio
async def test_models_come_from_the_cli_handshake(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        client.server_info = {"models": _HANDSHAKE_MODELS}
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        env=_GATEWAY_ENV,
        client_factory=factory,
    )

    models = await adapter.models()

    assert [model.id for model in models] == ["default", "haiku"]
    assert models[0].is_default is True
    assert models[0].name == "Default (recommended)"
    assert models[0].reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    # A model the CLI says has no effort support must not offer any.
    assert models[1].reasoning_efforts == []
    assert models[1].description == "openlite"
    assert clients[0].options.env == _GATEWAY_ENV
    assert clients[0].disconnected is True


@pytest.mark.asyncio
async def test_models_fall_back_to_aliases_when_the_cli_is_missing(
    tmp_path: Path,
) -> None:
    adapter = ClaudeAdapter(tmp_path, binary=str(tmp_path / "absent"))

    models = await adapter.models()

    assert [model.id for model in models] == [
        "default",
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]


@pytest.mark.asyncio
async def test_models_fall_back_to_aliases_when_the_handshake_fails(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")

    class BrokenClient(FakeClaudeClient):
        async def connect(self) -> None:
            raise CLIConnectionError("no transport")

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        env=_GATEWAY_ENV,
        client_factory=BrokenClient,
    )

    models = await adapter.models()
    opus = next(model for model in models if model.id == "opus")

    assert opus.description == "gpt-luna-max"


@pytest.mark.asyncio
async def test_models_fall_back_when_the_handshake_omits_models(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=FakeClaudeClient,
    )

    models = await adapter.models()

    assert [model.id for model in models][0] == "default"
    assert len(models) == 5


@pytest.mark.asyncio
async def test_turn_streams_events_and_persists_the_session(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        env=_GATEWAY_ENV,
        client_factory=factory,
    )

    events = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Fix it",
            cwd=tmp_path,
            settings={
                "model": "opus",
                "reasoning_effort": "high",
                "permission_mode": "default",
            },
        )
    ]

    assert [event.kind for event in events] == [
        HarnessEventKind.TEXT_DELTA,
        HarnessEventKind.COMPLETED,
    ]
    options = clients[-1].options
    assert options.cwd == tmp_path
    assert options.model == "opus"
    assert options.env == _GATEWAY_ENV
    assert options.extra_args == {
        "replay-user-messages": None,
        "effort": "high",
    }
    assert options.include_partial_messages is True
    assert options.session_id is not None
    assert options.resume is None
    assert clients[-1].prompts == [("Fix it", "default")]
    assert (tmp_path / "claude_sessions.json").is_file()


@pytest.mark.asyncio
async def test_default_model_alias_sends_no_model_argument(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Hi",
            cwd=tmp_path,
            settings={"model": "default"},
        )
    ]

    assert clients[0].options.model is None
    assert clients[0].options.extra_args == {"replay-user-messages": None}


@pytest.mark.asyncio
async def test_full_access_preset_skips_permission_checks(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Go",
            cwd=tmp_path,
            settings={"permission_mode": "bypassPermissions"},
        )
    ]

    options = clients[0].options
    assert options.permission_mode == "bypassPermissions"
    assert options.extra_args == {
        "replay-user-messages": None,
        "dangerously-skip-permissions": None,
    }


@pytest.mark.asyncio
async def test_second_turn_resumes_the_same_claude_session(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    for prompt in ("first", "second"):
        _ = [
            event
            async for event in adapter.run_turn(
                session_id="chat-1",
                prompt=prompt,
                cwd=tmp_path,
                settings={},
            )
        ]

    assert len(clients) == 1
    claude_session_id = adapter._sessions["chat-1"]
    assert clients[0].options.session_id == claude_session_id

    await adapter.stop()
    revived = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )
    _ = [
        event
        async for event in revived.run_turn(
            session_id="chat-1",
            prompt="third",
            cwd=tmp_path,
            settings={},
        )
    ]

    assert clients[-1].options.resume == claude_session_id
    assert clients[-1].options.session_id is None


@pytest.mark.asyncio
async def test_session_is_not_recorded_until_the_cli_answers(
    tmp_path: Path,
) -> None:
    # Claude Code writes the transcript a session id names only once a turn
    # produces output. Recording the id at connect time would leave a mapping
    # every later --resume rejects if the process dies mid-first-turn.
    binary = _executable(tmp_path / "claude")
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        if not clients:
            # The first CLI process exits before it answers.
            client.messages = [_STREAM_END]
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    # A turn the CLI abandons must fail, not complete with partial output.
    with pytest.raises(RuntimeError, match="exited before finishing"):
        _ = [
            event
            async for event in adapter.run_turn(
                session_id="chat-1",
                prompt="first",
                cwd=tmp_path,
                settings={},
            )
        ]

    assert adapter._sessions == {}
    assert not (tmp_path / "claude_sessions.json").exists()

    # The next turn reconnects under the same id and records it once output
    # flows.
    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="second",
            cwd=tmp_path,
            settings={},
        )
    ]

    assert len(clients) == 2
    assert clients[1].options.session_id == clients[0].options.session_id
    assert adapter._sessions["chat-1"] == clients[0].options.session_id


@pytest.mark.asyncio
async def test_refused_resume_starts_a_fresh_session_with_a_notice(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    stale = "550e8400-e29b-41d4-a716-446655440000"
    write_json_atomic(tmp_path / "claude_sessions.json", {"chat-1": stale})
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        if options.resume:
            async def refuse() -> None:
                raise ProcessError(
                    "Claude Code returned an error result: No conversation "
                    f"found with session ID: {options.resume}",
                    exit_code=1,
                )

            client.connect = refuse
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    events = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Go",
            cwd=tmp_path,
            settings={},
        )
    ]

    assert len(clients) == 2
    assert clients[0].options.resume == stale
    assert clients[1].options.resume is None
    assert clients[1].options.session_id not in (None, stale)
    assert events[0].kind == HarnessEventKind.TEXT_DELTA
    assert "no longer exists" in events[0].text
    assert adapter._sessions["chat-1"] == clients[1].options.session_id


@pytest.mark.asyncio
async def test_connect_failures_other_than_a_refused_resume_propagate(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)

        async def fail() -> None:
            raise ProcessError("Claude Code exited", exit_code=1)

        client.connect = fail
        return client

    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=factory,
    )

    with pytest.raises(ProcessError):
        _ = [
            event
            async for event in adapter.run_turn(
                session_id="chat-1",
                prompt="Go",
                cwd=tmp_path,
                settings={},
            )
        ]


@pytest.mark.asyncio
async def test_slash_commands_reach_the_cli_through_a_streamed_turn(
    tmp_path: Path,
) -> None:
    # The CLI owns slash-command parsing, so the adapter never batches one
    # through run_command; the prompt goes out verbatim and streams back.
    binary = _executable(tmp_path / "claude")
    client = FakeClaudeClient(ClaudeAgentOptions())
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=lambda _options: client,
    )

    events = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="/init",
            cwd=tmp_path,
            settings={},
        )
    ]

    assert client.prompts == [("/init", "default")]
    assert events[-1].kind == HarnessEventKind.COMPLETED
    assert not hasattr(ClaudeAdapter, "run_command") or (
        ClaudeAdapter.run_command is HarnessAdapter.run_command
    )


@pytest.mark.asyncio
async def test_claude_does_not_advertise_skill_discovery(
    tmp_path: Path,
) -> None:
    # The handshake carries no skills collection, only a flat command list
    # that mixes built-ins with skills and has no source field.
    adapter = ClaudeAdapter(tmp_path)

    assert await adapter.discover_skills(tmp_path) == []
    assert (
        get_provider("claude").capabilities.provider_skills_discovery is False
    )


@pytest.mark.asyncio
async def test_turn_projects_qwenpaw_skills_and_mcp(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    skill_dir = tmp_path / "workspace-skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n",
        encoding="utf-8",
    )
    clients: list[FakeClaudeClient] = []

    def factory(options: ClaudeAgentOptions) -> FakeClaudeClient:
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path / "harnesses",
        binary=str(binary),
        client_factory=factory,
    )
    capabilities = HarnessRuntimeCapabilities(
        skills=[
            HarnessSkillDefinition(
                name="review",
                description="Review code",
                directory=skill_dir,
            ),
        ],
        mcp_servers=[
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="streamable_http",
                url="https://mcp.example.test",
                headers={"Authorization": "Bearer secret"},
                tools=["search", "index", "purge"],
                tool_policies={
                    "search": "allow",
                    "index": "ask",
                    "purge": "deny",
                },
            ),
        ],
    )

    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Review it",
            cwd=tmp_path,
            settings={"_runtime_capabilities": capabilities},
        )
    ]

    options = clients[0].options
    assert options.skills == "all"
    assert options.setting_sources == ["user", "project", "local"]
    assert options.strict_mcp_config is True
    assert options.mcp_servers == {
        "docs": {
            "type": "http",
            "url": "https://mcp.example.test",
            "headers": {"Authorization": "Bearer secret"},
        },
    }
    assert options.allowed_tools == ["mcp__docs__search"]
    assert options.disallowed_tools == ["mcp__docs__purge"]
    plugin_path = Path(options.plugins[0]["path"])
    assert plugin_path.is_relative_to(tmp_path / "harnesses")
    assert (plugin_path / "skills" / "review" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_turn_sends_images_and_files_through_the_sdk_protocol(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    image = tmp_path / "media" / "mockup.png"
    image.parent.mkdir()
    image.write_bytes(b"image-bytes")
    document = tmp_path / "media" / "product brief.pdf"
    document.write_bytes(b"document-bytes")
    client = FakeClaudeClient(ClaudeAgentOptions())
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=lambda _options: client,
    )

    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Review both attachments",
            cwd=tmp_path,
            settings={},
            attachments=[
                HarnessAttachment(
                    kind=HarnessAttachmentKind.IMAGE,
                    path=image,
                    name=image.name,
                ),
                HarnessAttachment(
                    kind=HarnessAttachmentKind.FILE,
                    path=document,
                    name=document.name,
                ),
            ],
        )
    ]

    messages, query_session_id = client.prompts[0]
    assert query_session_id == "default"
    assert messages == [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '@"media/product brief.pdf"\n'
                            "Review both attachments"
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(b"image-bytes").decode(
                                "ascii",
                            ),
                        },
                    },
                ],
            },
            "parent_tool_use_id": None,
        },
    ]


@pytest.mark.asyncio
async def test_approval_uses_the_qwenpaw_service(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(tmp_path)
    adapter._contexts["chat-1"] = {
        "agent_id": "agent-1",
        "user_id": "user-1",
        "channel": "console",
    }
    pending = type(
        "Pending",
        (),
        {"request_id": "request-1", "timeout_seconds": 30},
    )()
    service = AsyncMock()
    service.create_pending_summary.return_value = pending
    service.wait_for_approval.return_value = ApprovalDecision.APPROVED
    context = type(
        "Context",
        (),
        {
            "description": "Run tests",
            "decision_reason": None,
            "display_name": "Shell",
            "title": None,
            "tool_use_id": "tool-1",
            "blocked_path": None,
        },
    )()

    with patch(
        "qwenpaw.harnesses.claude.adapter.get_approval_service",
        return_value=service,
    ):
        result = await adapter._approve_tool(
            "chat-1",
            "Bash",
            {"command": "pytest"},
            context,
        )

    assert result.behavior == "allow"
    summary = service.create_pending_summary.call_args.kwargs["summary"]
    assert summary.source_type == "claude"
    assert summary.payload["provider"] == "claude"
    assert summary.payload["tool_name"] == "Bash"


@pytest.mark.asyncio
async def test_rejected_approval_denies_the_tool(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(tmp_path)
    pending = type(
        "Pending",
        (),
        {"request_id": "request-1", "timeout_seconds": 30},
    )()
    service = AsyncMock()
    service.create_pending_summary.return_value = pending
    service.wait_for_approval.return_value = ApprovalDecision.DENIED
    context = type(
        "Context",
        (),
        {
            "description": None,
            "decision_reason": "writes outside workspace",
            "display_name": None,
            "title": None,
            "tool_use_id": "tool-1",
            "blocked_path": "/etc/hosts",
        },
    )()

    with patch(
        "qwenpaw.harnesses.claude.adapter.get_approval_service",
        return_value=service,
    ):
        result = await adapter._approve_tool(
            "chat-1",
            "Write",
            {"path": "/etc/hosts"},
            context,
        )

    assert result.behavior == "deny"


@pytest.mark.asyncio
async def test_history_uses_the_persisted_claude_session(
    tmp_path: Path,
) -> None:
    adapter = ClaudeAdapter(tmp_path)
    adapter._sessions["chat-1"] = "550e8400-e29b-41d4-a716-446655440000"
    messages = [
        SessionMessage(
            type="assistant",
            uuid="assistant-1",
            session_id=adapter._sessions["chat-1"],
            message={"content": [{"type": "text", "text": "Recovered"}]},
        ),
    ]

    with patch(
        "qwenpaw.harnesses.claude.adapter.get_session_messages",
        return_value=messages,
    ) as get_messages:
        history = await adapter.history("chat-1")

    assert history[0].text == "Recovered"
    get_messages.assert_called_once_with(
        adapter._sessions["chat-1"],
        None,
    )


@pytest.mark.asyncio
async def test_reset_session_forgets_the_local_mapping(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "claude")
    client = FakeClaudeClient(ClaudeAgentOptions())
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=lambda _options: client,
    )
    _ = [
        event
        async for event in adapter.run_turn(
            session_id="chat-1",
            prompt="Hi",
            cwd=tmp_path,
            settings={},
        )
    ]

    await adapter.reset_session("chat-1")

    assert adapter._sessions == {}
    assert adapter._clients == {}
    assert adapter._readers == {}
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_cancelling_a_turn_interrupts_claude(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")
    response_started = asyncio.Event()

    class SilentClaudeClient(FakeClaudeClient):
        async def query(self, prompt: Any, session_id: str = "default"):
            self.prompts.append((prompt, session_id))
            response_started.set()

    client = SilentClaudeClient(ClaudeAgentOptions())
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(binary),
        client_factory=lambda _options: client,
    )

    async def consume() -> None:
        async for _ in adapter.run_turn(
            session_id="chat-1",
            prompt="Wait",
            cwd=tmp_path,
            settings={},
        ):
            pass

    turn = asyncio.create_task(consume())
    await response_started.wait()
    turn.cancel()

    with pytest.raises(asyncio.CancelledError):
        await turn

    assert client.interrupted is True


@pytest.mark.asyncio
async def test_cli_timeout_reaps_process(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "claude")

    class HangingProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return -9

    process = HangingProcess()
    adapter = ClaudeAdapter(tmp_path, binary=str(binary))
    with (
        patch(
            "qwenpaw.harnesses.claude.adapter."
            "asyncio.create_subprocess_exec",
            return_value=process,
        ),
        patch(
            "qwenpaw.harnesses.claude.adapter._STATUS_TIMEOUT_SECONDS",
            0.01,
        ),
        pytest.raises(asyncio.TimeoutError),
    ):
        await adapter._run_cli("auth", "status", "--json")

    assert process.killed is True


@pytest.mark.parametrize("cleanup_failure", [None, "interrupt", "disconnect"])
async def test_cancelled_turn_reconnects_without_stale_result(
    tmp_path: Path, cleanup_failure: str | None,
) -> None:
    started = asyncio.Event()
    clients = []

    class QueuedClient(FakeClaudeClient):
        async def query(self, prompt: Any, session_id: str = "default"):
            if clients[0] is not self:
                await super().query(prompt, session_id)
                return
            # The first turn streams one delta, then hangs.
            self.prompts.append((prompt, session_id))
            self.inbox.put_nowait(self.messages[0])

        async def interrupt(self):
            await super().interrupt()
            # Interrupt leaves a terminal result a reused client would see.
            self.inbox.put_nowait(self.messages[-1])
            if cleanup_failure == "interrupt":
                raise RuntimeError("interrupt failed")

        async def disconnect(self):
            await super().disconnect()
            if cleanup_failure == "disconnect":
                raise RuntimeError("disconnect failed")

    def factory(options):
        client = QueuedClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=factory,
    )

    async def consume():
        events = []
        async for event in adapter.run_turn(
            session_id="chat", prompt="Hi", cwd=tmp_path, settings={},
        ):
            events.append(event)
            started.set()
        return events

    task = asyncio.create_task(consume())
    await started.wait()
    resume_id = adapter._sessions["chat"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = await consume()
    assert any(event.text == "Done" for event in events)
    assert len(clients) == 2
    assert clients[0].disconnected
    assert clients[1].options.resume == resume_id


@pytest.mark.parametrize("default_policy", ["allow", "ask", "deny"])
async def test_empty_mcp_allowlist_is_not_connected(tmp_path, default_policy):
    adapter = ClaudeAdapter(tmp_path, binary=str(_executable(tmp_path / "claude")))
    options = adapter._options(
        session_id="chat", claude_session_id="id", is_resume=False,
        cwd=tmp_path, plugin_path=None, settings={
            "_runtime_capabilities": HarnessRuntimeCapabilities(mcp_servers=[
                HarnessMCPServerDefinition(
                    name="docs", display_name="Docs", transport="stdio",
                    command="docs", tools=[], default_policy=default_policy,
                ),
            ]),
        },
    )
    assert options.mcp_servers == {}


@pytest.mark.parametrize("mode", ["default", "bypassPermissions"])
@pytest.mark.parametrize("tools,default,policies,tool,effect", [
    (["read"], "allow", {}, "write", "deny"),
    (["read"], "allow", {}, "read", "allow"),
    (["read"], "deny", {"read": "allow"}, "read", "allow"),
    (["read"], "allow", {"read": "deny"}, "read", "deny"),
    (None, "allow", {}, "write", "allow"),
    (None, "deny", {}, "write", "deny"),
    (None, "ask", {}, "read", "ask"),
    (["read"], "allow", {"read": "ask"}, "read", "ask"),
])
@pytest.mark.parametrize("approved", [False, True])
async def test_mcp_pre_execution_policy(
    tmp_path, mode, tools, default, policies, tool, effect, approved,
):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    adapter = ClaudeAdapter(tmp_path, binary=str(_executable(tmp_path / "claude")))
    options = adapter._options(
        session_id="chat", claude_session_id="id", is_resume=False,
        cwd=tmp_path, plugin_path=None, settings={
            "permission_mode": mode,
            "_runtime_capabilities": HarnessRuntimeCapabilities(mcp_servers=[
                HarnessMCPServerDefinition(
                    name="docs", display_name="Docs", transport="stdio",
                    command="docs", tools=tools, default_policy=default,
                    tool_policies=policies,
                ),
            ]),
        },
    )
    approval = AsyncMock(return_value=(
        PermissionResultAllow() if approved else PermissionResultDeny()
    ))
    adapter._approve_tool = approval
    assert options.hooks and options.hooks.get("PreToolUse")
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook({
        "tool_name": f"mcp__docs__{tool}", "tool_input": {"x": 1},
    }, "call-1", {})
    expected = ("allow" if approved else "deny") if effect == "ask" else effect
    assert result["hookSpecificOutput"]["permissionDecision"] == expected
    assert approval.await_count == (1 if effect == "ask" else 0)
    assert await hook({"tool_name": "Read"}, "call-2", {}) == {}
    if expected == "allow":
        assert "mcp__docs__*" not in options.disallowed_tools


async def test_mcp_hook_matches_longest_managed_server_name(tmp_path):
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(_executable(tmp_path / "claude")),
    )
    capabilities = HarnessRuntimeCapabilities(mcp_servers=[
        HarnessMCPServerDefinition(
            name="docs", display_name="Docs", transport="stdio",
            command="docs", default_policy="deny",
        ),
        HarnessMCPServerDefinition(
            name="docs__private", display_name="Private", transport="stdio",
            command="docs", tools=None, default_policy="deny",
            tool_policies={"read": "allow"},
        ),
    ])
    options = adapter._options(
        session_id="chat", claude_session_id="id", is_resume=False,
        cwd=tmp_path, plugin_path=None,
        settings={"_runtime_capabilities": capabilities},
    )
    assert set(options.mcp_servers) == {"docs__private"}
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook(
        {"tool_name": "mcp__docs__private__read"}, "call", {},
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert await hook(
        {"tool_name": "mcp__unmanaged__read"}, "call", {},
    ) == {}


async def test_mcp_policy_uses_the_cli_server_name(tmp_path):
    # Claude Code names tools mcp__<server>__<tool>, with every character of
    # the server name outside [a-zA-Z0-9_-] replaced by "_".
    adapter = ClaudeAdapter(
        tmp_path,
        binary=str(_executable(tmp_path / "claude")),
    )
    capabilities = HarnessRuntimeCapabilities(mcp_servers=[
        HarnessMCPServerDefinition(
            name="my docs.v2", display_name="Docs", transport="stdio",
            command="docs", tools=None, default_policy="allow",
            tool_policies={"purge": "deny"},
        ),
    ])
    options = adapter._options(
        session_id="chat", claude_session_id="id", is_resume=False,
        cwd=tmp_path, plugin_path=None,
        settings={
            "permission_mode": "bypassPermissions",
            "_runtime_capabilities": capabilities,
        },
    )
    assert options.disallowed_tools == ["mcp__my_docs_v2__purge"]
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook(
        {"tool_name": "mcp__my_docs_v2__purge"}, "call", {},
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_mcp_approval_failure_returns_explicit_deny(
    tmp_path, monkeypatch, failure,
):
    monkeypatch.setattr(
        "qwenpaw.harnesses.claude.adapter."
        "TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS",
        0.01,
    )
    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
    )
    capabilities = HarnessRuntimeCapabilities(mcp_servers=[
        HarnessMCPServerDefinition(
            name="docs", display_name="Docs", transport="stdio",
            command="docs", default_policy="ask",
        ),
    ])
    options = adapter._options(
        session_id="chat", claude_session_id="id", is_resume=False,
        cwd=tmp_path, plugin_path=None, settings={
            "permission_mode": "bypassPermissions",
            "_runtime_capabilities": capabilities,
        },
    )

    async def broken_approval(*args):
        if failure == "error":
            raise RuntimeError("approval service unavailable")
        await asyncio.Event().wait()

    adapter._approve_tool = broken_approval
    hook = options.hooks["PreToolUse"][0].hooks[0]
    async with asyncio.timeout(1):
        result = await hook(
            {"tool_name": "mcp__docs__read"}, "call", {},
        )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_mcp_approval_cancellation_propagates(tmp_path):
    adapter = ClaudeAdapter(tmp_path)
    capabilities = HarnessRuntimeCapabilities(mcp_servers=[
        HarnessMCPServerDefinition(
            name="docs", display_name="Docs", transport="stdio",
            command="docs", default_policy="ask",
        ),
    ])
    started = asyncio.Event()

    async def pending_approval(*args):
        started.set()
        await asyncio.Event().wait()

    adapter._approve_tool = pending_approval
    task = asyncio.create_task(adapter._gate_mcp_tool(
        "chat", capabilities, {"tool_name": "mcp__docs__read"}, "call",
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_closing_turn_at_yield_evicts_client_and_resumes(tmp_path):
    clients = []

    def factory(options):
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=factory,
    )
    stream = adapter.run_turn(
        session_id="chat", prompt="Hi", cwd=tmp_path, settings={},
    )
    assert (await anext(stream)).text == "Done"
    resume_id = adapter._sessions["chat"]
    await stream.aclose()
    assert clients[0].interrupted
    assert clients[0].disconnected
    assert "chat" not in adapter._clients
    events = [event async for event in adapter.run_turn(
        session_id="chat", prompt="Again", cwd=tmp_path, settings={},
    )]
    assert any(event.text == "Done" for event in events)
    assert clients[1].options.resume == resume_id


async def test_closing_reset_notice_evicts_fresh_client(tmp_path):
    write_json_atomic(
        tmp_path / "claude_sessions.json", {"chat": "stale"},
    )
    clients = []

    def factory(options):
        client = FakeClaudeClient(options)
        if options.resume:
            client.connect = AsyncMock(side_effect=ProcessError(
                "No conversation found with session ID: stale", exit_code=1,
            ))
        clients.append(client)
        return client

    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=factory,
    )
    stream = adapter.run_turn(
        session_id="chat", prompt="Hi", cwd=tmp_path, settings={},
    )
    assert "no longer exists" in (await anext(stream)).text
    fresh = clients[-1]
    assert not fresh.prompts
    await stream.aclose()
    assert fresh.interrupted
    assert fresh.disconnected
    assert "chat" not in adapter._clients
    assert "chat" not in adapter._pending


def _text_delta(text: str) -> StreamEvent:
    return StreamEvent(
        uuid=f"stream-{text}",
        session_id="claude-session",
        event={
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    )


def _turn(text: str) -> list[Any]:
    """One CLI turn: init, a streamed reply, and its result."""
    return [
        SystemMessage(subtype="init", data={}),
        _text_delta(text),
        FakeClaudeClient(ClaudeAgentOptions()).messages[-1],
    ]


async def _drained(client: FakeClaudeClient) -> None:
    for _ in range(100):
        if client.inbox.empty():
            return
        await asyncio.sleep(0)
    raise AssertionError("the adapter stopped reading the client")


def _texts(events) -> list[str]:
    return [
        event.text for event in events
        if event.kind == HarnessEventKind.TEXT_DELTA
    ]


async def test_messages_between_turns_are_drained_not_replayed(tmp_path):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=lambda _options: client,
    )

    async def turn(prompt: str):
        return [event async for event in adapter.run_turn(
            session_id="chat", prompt=prompt, cwd=tmp_path, settings={},
        )]

    assert _texts(await turn("one")) == ["First"]

    # A background agent wakes the session and finishes a whole CLI turn,
    # with more output than the SDK buffers, while no QwenPaw turn runs.
    for message in [
        SystemMessage(subtype="init", data={}),
        *[_text_delta("Background") for _ in range(150)],
        client.messages[-1],
    ]:
        client.inbox.put_nowait(message)
    await _drained(client)

    client.messages = _turn("Second")
    assert _texts(await turn("two")) == ["Second"]


async def test_prompt_during_background_turn_skips_that_turn(tmp_path):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=lambda _options: client,
    )
    _ = [event async for event in adapter.run_turn(
        session_id="chat", prompt="one", cwd=tmp_path, settings={},
    )]

    # The wake-up turn is still open when the next prompt is sent; the CLI
    # finishes it before it runs the prompt.
    client.inbox.put_nowait(SystemMessage(subtype="init", data={}))
    client.inbox.put_nowait(_text_delta("Background"))
    await _drained(client)
    client.messages = [
        _text_delta("More background"),
        client.messages[-1],
        *_turn("Second"),
    ]

    events = [event async for event in adapter.run_turn(
        session_id="chat", prompt="two", cwd=tmp_path, settings={},
    )]

    assert _texts(events) == ["Second"]
    assert events[-1].kind == HarnessEventKind.COMPLETED


async def test_prompt_folded_into_background_turn_ends_with_it(tmp_path):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=lambda _options: client,
    )
    _ = [event async for event in adapter.run_turn(
        session_id="chat", prompt="one", cwd=tmp_path, settings={},
    )]

    # A wake-up turn is running a tool when the prompt arrives; the CLI
    # appends the prompt to that turn after the tool result.
    client.inbox.put_nowait(SystemMessage(subtype="init", data={}))
    client.inbox.put_nowait(_text_delta("Background"))
    await _drained(client)
    client.messages = [
        UserMessage(content=[ToolResultBlock(tool_use_id="toolu_bg")]),
        UserMessage(content="two", uuid="echo-two"),
        _text_delta("Second"),
        client.messages[-1],
    ]

    events = await asyncio.wait_for(
        _collect(adapter.run_turn(
            session_id="chat", prompt="two", cwd=tmp_path, settings={},
        )),
        timeout=5,
    )

    assert _texts(events) == ["Second"]
    assert events[-1].kind == HarnessEventKind.COMPLETED


async def _collect(stream) -> list[Any]:
    return [event async for event in stream]


class _ScriptedTransport:
    """CLI stand-in for a real ClaudeSDKClient: answers the handshake, ends
    each prompt with a result, and lets the test emit later frames."""

    def __init__(self) -> None:
        self.frames: asyncio.Queue[Any] = asyncio.Queue()
        self.control_responses: list[dict[str, Any]] = []

    async def connect(self) -> None:
        return None

    async def write(self, data: str) -> None:
        message = json.loads(data)
        if message.get("type") == "control_response":
            self.control_responses.append(message["response"])
        elif message.get("type") == "control_request":
            self.frames.put_nowait({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": message["request_id"],
                    "response": {},
                },
            })
        elif message.get("type") == "user":
            for frame in (
                {"type": "system", "subtype": "init"},
                {
                    "type": "result", "subtype": "success",
                    "duration_ms": 1, "duration_api_ms": 1,
                    "is_error": False, "num_turns": 1,
                    "session_id": "claude-session",
                },
            ):
                self.frames.put_nowait(frame)

    async def read_messages(self):
        while True:
            frame = await self.frames.get()
            if frame is _STREAM_END:
                return
            yield frame

    async def close(self) -> None:
        self.frames.put_nowait(_STREAM_END)

    async def end_input(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True


async def test_background_permission_request_is_answered_after_turn(
    tmp_path,
):
    transport = _ScriptedTransport()
    adapter = ClaudeAdapter(
        tmp_path, binary=str(_executable(tmp_path / "claude")),
        client_factory=lambda options: ClaudeSDKClient(
            options, transport=transport,
        ),
    )
    approved = asyncio.Event()

    async def approve(*_args):
        approved.set()
        return PermissionResultAllow()

    adapter._approve_tool = approve
    try:
        _ = [event async for event in adapter.run_turn(
            session_id="chat", prompt="Start", cwd=tmp_path, settings={},
        )]

        # A background agent keeps streaming past the SDK's 100-message
        # buffer, then asks to run a tool, with no QwenPaw turn running.
        for index in range(150):
            transport.frames.put_nowait({
                "type": "stream_event", "uuid": f"bg-{index}",
                "session_id": "claude-session",
                "event": {"type": "content_block_delta"},
                "parent_tool_use_id": "toolu_background",
            })
        transport.frames.put_nowait({
            "type": "control_request", "request_id": "perm-1",
            "request": {
                "subtype": "can_use_tool", "tool_name": "Bash",
                "input": {"command": "ls"},
                "permission_suggestions": None,
            },
        })

        await asyncio.wait_for(approved.wait(), timeout=5)
        for _ in range(100):
            if any(
                response.get("request_id") == "perm-1"
                for response in transport.control_responses
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the permission answer never reached the CLI")
    finally:
        await adapter.stop()
