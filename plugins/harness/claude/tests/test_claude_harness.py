# -*- coding: utf-8 -*-
"""Claude Code backend scenarios on the public harness contract."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    ResultMessage,
    SessionMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    UserMessage,
)
from qwenpaw.plugins import SDKError
from qwenpaw.plugins.harnesses import (
    HarnessAttachment,
    HarnessAttachmentKind,
    HarnessCallContext,
    HarnessContext,
    HarnessEventKind,
    HarnessMCPServerDefinition,
    HarnessOperationNotSupportedError,
    HarnessRunRequest,
    HarnessRuntimeCapabilities,
    HarnessSkillDefinition,
)
from qwenpaw.plugins.resources import ResourceOwner, ResourceScope
from qwenpaw.plugins.storage import PluginStorage
from plugins.harness.claude import adapter as adapter_module
from plugins.harness.claude.adapter import ClaudeBackend
from plugins.harness.claude.main import Plugin

_PLUGIN_ID = "qwenpaw-harness-claude"
_IDENTITY = json.dumps(["alice", "console", "chat"])
_NATIVE = "550e8400-e29b-41d4-a716-446655440000"
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
# The console editor stores env as ordered rows.
_GATEWAY_ROWS = [
    {"name": name, "value": value} for name, value in _GATEWAY_ENV.items()
]

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
            _text_delta("Done"),
            AssistantMessage(
                content=[TextBlock(text="Done")],
                model="claude-test",
            ),
            _result(),
        ]

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        if not isinstance(prompt, str):
            prompt = [message async for message in prompt]
        self.prompts.append((prompt, session_id))
        for message in self.messages:
            self.inbox.put_nowait(message)

    async def receive_messages(self):
        while True:
            message = await self.inbox.get()
            if message is _STREAM_END:
                return
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True

    async def get_server_info(self) -> dict[str, Any]:
        return self.server_info


def _text_delta(text: str) -> StreamEvent:
    return StreamEvent(
        uuid=f"stream-{text}",
        session_id="claude-session",
        event={
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    )


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="claude-session",
    )


def _turn(text: str) -> list[Any]:
    """One CLI turn: init, a streamed reply, and its result."""
    return [
        SystemMessage(subtype="init", data={}),
        _text_delta(text),
        _result(),
    ]


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(path.stat().st_mode | 0o111)
    return path


def _texts(events) -> list[str]:
    return [
        event.text
        for event in events
        if event.kind == HarnessEventKind.TEXT_DELTA
    ]


@pytest.fixture(name="runtime")
async def make_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_PATH", raising=False)
    binary = _executable(tmp_path / "bin" / "claude")
    state = tmp_path / "workspace" / "plugin-data" / _PLUGIN_ID
    state.mkdir(parents=True)
    owner = ResourceOwner(_PLUGIN_ID, 1, "workspace")
    context = HarnessContext(
        owner.plugin_id,
        1,
        "workspace",
        state.parents[1],
        state,
        PluginStorage(state, lambda: None),
        ResourceScope(owner, lambda: None),
        SimpleNamespace(),
    )
    clients: list[FakeClaudeClient] = []
    backends: list[ClaudeBackend] = []

    def factory(options):
        client = FakeClaudeClient(options)
        clients.append(client)
        return client

    def backend(**kwargs):
        kwargs.setdefault("binary", str(binary))
        kwargs.setdefault("client_factory", factory)
        value = ClaudeBackend(context, **kwargs)
        backends.append(value)
        return value

    def call(
        user="alice",
        session="chat",
        *,
        settings=None,
        capabilities=None,
        **kwargs,
    ):
        return HarnessCallContext(
            f"{user}-{session}",
            session,
            user,
            "console",
            context.workspace_dir,
            dict(settings or {}),
            capabilities,
            context.resources.child(instance_id="call"),
            **kwargs,
        )

    yield SimpleNamespace(
        context=context,
        call=call,
        backend=backend,
        clients=clients,
        binary=binary,
        sessions=state / "claude_sessions.json",
    )
    for value in backends:
        with contextlib.suppress(Exception):
            await value.close()


async def collect(backend, call, prompt="Hi", attachments=()):
    request = HarnessRunRequest(prompt, tuple(attachments))
    async with asyncio.timeout(5):
        return [event async for event in backend.run_turn(call, request)]


async def _drained(client: FakeClaudeClient) -> None:
    for _ in range(100):
        if client.inbox.empty():
            return
        await asyncio.sleep(0)
    raise AssertionError("the backend stopped reading the client")


# Registration


def test_registration_exposes_claude_capabilities():
    specs = []
    Plugin().register(SimpleNamespace(register_harness=specs.append))
    (spec,) = specs
    capabilities = spec.capabilities

    assert (spec.id, spec.label) == ("claude", "Claude Code")
    assert spec.instance_settings_keys == ("binary",)
    assert capabilities.model_selection is True
    assert capabilities.reasoning_stream is True
    assert capabilities.tool_stream is True
    assert capabilities.attachments is True
    assert capabilities.provider_skills_discovery is False
    assert capabilities.provider_mcp_discovery is False
    assert capabilities.mcp_tool_allowlist is True
    assert capabilities.passthrough_commands is True
    assert [command.name for command in capabilities.commands] == [
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
        command.name for command in capabilities.commands
    }
    modes = [
        preset.settings["permission_mode"]
        for preset in capabilities.approval_presets
    ]
    assert modes == [
        "default",
        "acceptEdits",
        "plan",
        "auto",
        "bypassPermissions",
    ]


# Accounts and status


async def test_login_declines_with_actionable_guidance(runtime):
    with pytest.raises(
        HarnessOperationNotSupportedError,
        match="ANTHROPIC_AUTH_TOKEN",
    ):
        await runtime.backend().start_login(runtime.call())


async def test_logout_delegates_to_the_cli(runtime):
    backend, call = runtime.backend(), runtime.call()
    backend._run_cli = AsyncMock(return_value="")

    await backend.logout(call)

    backend._run_cli.assert_awaited_once_with(
        call,
        "auth",
        "logout",
        check=True,
    )


async def test_logout_reports_a_failing_cli(runtime, tmp_path):
    binary = tmp_path / "failing" / "claude"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\necho 'not signed in' >&2\nexit 3\n")
    binary.chmod(0o755)

    with pytest.raises(SDKError, match="exit 3"):
        await runtime.backend(binary=str(binary)).logout(runtime.call())


@pytest.mark.parametrize(
    ("output", "authenticated", "account"),
    [
        (
            _LOGGED_IN,
            True,
            {
                "type": "claude.ai",
                "email": "user@example.com",
                "organization": "Example",
                "plan": "team",
            },
        ),
        (
            json.dumps({"loggedIn": True, "authMethod": "oauth_token"}),
            True,
            {"type": "oauth_token"},
        ),
        (json.dumps({"loggedIn": False}), False, None),
        ("error: something went wrong", False, None),
    ],
)
async def test_status_reports_the_cli_account(
    runtime,
    output,
    authenticated,
    account,
):
    backend, call = runtime.backend(), runtime.call()
    backend._run_cli = AsyncMock(return_value=output)

    status = await backend.status(call)

    backend._run_cli.assert_awaited_once_with(
        call,
        "auth",
        "status",
        "--json",
    )
    assert status.installed is True
    assert status.runtime_path == str(runtime.binary)
    assert status.runtime_source == "configured"
    assert status.authenticated is authenticated
    assert status.account == account


async def test_status_reports_a_missing_installation(runtime, tmp_path):
    backend = runtime.backend(binary=str(tmp_path / "absent"))

    status = await backend.status(runtime.call())

    assert status.installed is False
    assert "not found" in status.error


async def test_status_probe_runs_under_the_agent_environment(
    runtime,
    monkeypatch,
):
    process = AsyncMock()
    process.communicate.return_value = (_LOGGED_IN.encode("utf-8"), b"")
    process.returncode = 0
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    status = await runtime.backend().status(
        runtime.call(settings={"env": _GATEWAY_ROWS}),
    )

    assert status.authenticated is True
    arguments = spawn.call_args.args
    passed_env = spawn.call_args.kwargs["env"]
    assert arguments[1:] == ("auth", "status", "--json")
    assert passed_env["ANTHROPIC_BASE_URL"] == (
        "https://gateway.example.test/v1"
    )
    assert "PATH" in passed_env


async def test_cli_timeout_reaps_process(runtime, monkeypatch):
    class HangingProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.killed = False

        async def communicate(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return b"", b""

        def terminate(self) -> None:
            self.killed = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode

    process = HangingProcess()
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(adapter_module, "_STATUS_TIMEOUT_SECONDS", 0.01)
    backend, call = runtime.backend(), runtime.call()

    with pytest.raises(asyncio.TimeoutError):
        await backend._run_cli(call, "auth", "status", "--json")

    assert process.killed is True
    assert not backend._processes


# Models


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


async def test_models_come_from_the_cli_handshake(runtime):
    clients = []

    def factory(options):
        client = FakeClaudeClient(options)
        client.server_info = {"models": _HANDSHAKE_MODELS}
        clients.append(client)
        return client

    backend = runtime.backend(client_factory=factory)

    models = await backend.models(
        runtime.call(settings={"env": _GATEWAY_ROWS}),
    )

    assert [model.id for model in models] == ["default", "haiku"]
    assert models[0].is_default is True
    assert models[0].name == "Default (recommended)"
    assert models[0].reasoning_efforts == (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    # A model the CLI says has no effort support must not offer any.
    assert models[1].reasoning_efforts == ()
    assert models[1].description == "openlite"
    assert clients[0].options.env == _GATEWAY_ENV
    assert clients[0].options.setting_sources == []
    assert clients[0].disconnected is True
    assert not backend._owned_clients


async def test_models_fall_back_to_aliases_when_the_cli_is_missing(
    runtime,
    tmp_path,
):
    backend = runtime.backend(binary=str(tmp_path / "absent"))

    models = await backend.models(runtime.call())

    assert [model.id for model in models] == [
        "default",
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert not runtime.clients


async def test_models_fall_back_to_aliases_when_the_handshake_fails(runtime):
    class BrokenClient(FakeClaudeClient):
        async def connect(self) -> None:
            raise CLIConnectionError("no transport")

    backend = runtime.backend(client_factory=BrokenClient)

    models = await backend.models(
        runtime.call(settings={"env": _GATEWAY_ENV}),
    )
    opus = next(model for model in models if model.id == "opus")

    assert opus.description == "gpt-luna-max"


async def test_models_fall_back_when_the_handshake_omits_models(runtime):
    models = await runtime.backend().models(runtime.call())

    assert models[0].id == "default"
    assert len(models) == 5


# Turns and session persistence


async def test_turn_streams_events_and_persists_the_session(runtime):
    backend = runtime.backend()
    call = runtime.call(
        settings={
            "model": "opus",
            "reasoning_effort": "high",
            "permission_mode": "default",
            "env": _GATEWAY_ROWS,
        },
    )

    events = await collect(backend, call, "Fix it")

    assert [event.kind for event in events] == [
        HarnessEventKind.TEXT_DELTA,
        HarnessEventKind.COMPLETED,
    ]
    options = runtime.clients[-1].options
    assert options.cwd == call.cwd
    assert options.model == "opus"
    assert options.env == _GATEWAY_ENV
    assert options.extra_args == {
        "replay-user-messages": None,
        "effort": "high",
    }
    assert options.include_partial_messages is True
    assert options.session_id is not None
    assert options.resume is None
    assert runtime.clients[-1].prompts == [("Fix it", "default")]
    assert json.loads(runtime.sessions.read_text()) == {
        _IDENTITY: options.session_id,
    }


async def test_default_model_alias_sends_no_model_argument(runtime):
    await collect(
        runtime.backend(),
        runtime.call(settings={"model": "default"}),
    )

    assert runtime.clients[0].options.model is None
    assert runtime.clients[0].options.extra_args == {
        "replay-user-messages": None,
    }


async def test_full_access_preset_skips_permission_checks(runtime):
    await collect(
        runtime.backend(),
        runtime.call(settings={"permission_mode": "bypassPermissions"}),
    )

    options = runtime.clients[0].options
    assert options.permission_mode == "bypassPermissions"
    assert options.extra_args == {
        "replay-user-messages": None,
        "dangerously-skip-permissions": None,
    }


async def test_changed_environment_reconnects_without_exposing_values(
    runtime,
):
    backend = runtime.backend()
    await collect(backend, runtime.call())
    await collect(backend, runtime.call())
    assert len(runtime.clients) == 1

    await collect(backend, runtime.call(settings={"env": _GATEWAY_ROWS}))

    assert len(runtime.clients) == 2
    assert runtime.clients[0].disconnected is True
    assert runtime.clients[1].options.resume is not None
    key = backend._client_keys[_IDENTITY]
    assert not any("gateway" in str(part) for part in key)


async def test_second_turn_resumes_the_same_claude_session(runtime):
    backend, call = runtime.backend(), runtime.call()
    for prompt in ("first", "second"):
        await collect(backend, call, prompt)

    assert len(runtime.clients) == 1
    native_id = backend._sessions[_IDENTITY]
    assert runtime.clients[0].options.session_id == native_id

    await backend.close()
    revived = runtime.backend()
    await collect(revived, call, "third")

    assert runtime.clients[-1].options.resume == native_id
    assert runtime.clients[-1].options.session_id is None


async def test_legacy_session_mapping_is_copied_and_claimed(runtime):
    legacy = (
        runtime.context.workspace_dir / "harnesses" / "claude_sessions.json"
    )
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"chat": _NATIVE}))
    backend, call = runtime.backend(), runtime.call()

    await collect(backend, call)

    assert runtime.clients[-1].options.resume == _NATIVE
    assert json.loads(runtime.sessions.read_text()) == {_IDENTITY: _NATIVE}
    # The legacy file stays untouched for a rollback to the in-core backend.
    assert json.loads(legacy.read_text()) == {"chat": _NATIVE}
    await collect(backend, runtime.call("bob"))
    assert runtime.clients[-1].options.resume is None
    assert runtime.clients[-1].options.session_id != _NATIVE

    await backend.close()
    revived = runtime.backend()
    await collect(revived, call)
    assert runtime.clients[-1].options.resume == _NATIVE


async def test_invalid_legacy_mapping_fails_closed(runtime):
    legacy = (
        runtime.context.workspace_dir / "harnesses" / "claude_sessions.json"
    )
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"chat": 7}))

    with pytest.raises(SDKError, match="invalid"):
        await runtime.backend().prepare()
    assert not runtime.sessions.exists()


async def test_session_is_not_recorded_until_the_cli_answers(runtime):
    # Claude Code writes the transcript a session id names only once a turn
    # produces output. Recording the id at connect time would leave a mapping
    # every later --resume rejects if the process dies mid-first-turn.
    clients = []

    def factory(options):
        client = FakeClaudeClient(options)
        if not clients:
            # The first CLI process exits before it answers.
            client.messages = [_STREAM_END]
        clients.append(client)
        return client

    backend, call = runtime.backend(client_factory=factory), runtime.call()

    # A turn the CLI abandons must fail, not complete with partial output.
    with pytest.raises(RuntimeError, match="exited before finishing"):
        await collect(backend, call, "first")

    assert backend._sessions == {}
    assert not runtime.sessions.exists()

    # The next turn reconnects under the same id and records it once output
    # flows.
    await collect(backend, call, "second")

    assert len(clients) == 2
    assert clients[0].disconnected is True
    assert clients[1].options.session_id == clients[0].options.session_id
    assert backend._sessions[_IDENTITY] == clients[0].options.session_id


def _refusing_factory(clients):
    def factory(options):
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

    return factory


async def test_refused_resume_starts_a_fresh_session_with_a_notice(runtime):
    runtime.sessions.write_text(json.dumps({_IDENTITY: _NATIVE}))
    clients = []
    backend = runtime.backend(client_factory=_refusing_factory(clients))

    events = await collect(backend, runtime.call(), "Go")

    assert len(clients) == 2
    assert clients[0].options.resume == _NATIVE
    assert clients[0].disconnected is True
    assert clients[1].options.resume is None
    assert clients[1].options.session_id not in (None, _NATIVE)
    assert events[0].kind == HarnessEventKind.TEXT_DELTA
    assert "no longer exists" in events[0].text
    assert backend._sessions[_IDENTITY] == clients[1].options.session_id


async def test_connect_failures_other_than_a_refused_resume_propagate(
    runtime,
):
    def factory(options):
        client = FakeClaudeClient(options)

        async def fail() -> None:
            raise ProcessError("Claude Code exited", exit_code=1)

        client.connect = fail
        return client

    backend = runtime.backend(client_factory=factory)

    with pytest.raises(SDKError) as raised:
        await collect(backend, runtime.call(), "Go")
    assert isinstance(raised.value.__cause__, ProcessError)
    assert backend._sessions == {}


async def test_slash_commands_reach_the_cli_through_a_streamed_turn(runtime):
    # The CLI owns slash-command parsing, so the prompt goes out verbatim
    # and streams back.
    events = await collect(runtime.backend(), runtime.call(), "/init")

    assert runtime.clients[0].prompts == [("/init", "default")]
    assert events[-1].kind == HarnessEventKind.COMPLETED


async def test_run_command_drains_the_same_streamed_turn(runtime):
    # Only a host without passthrough calls run_command.
    events = await runtime.backend().run_command(
        runtime.call(),
        "compact",
        "keep the plan",
    )

    assert runtime.clients[0].prompts == [
        ("/compact keep the plan", "default"),
    ]
    assert events[-1].kind == HarnessEventKind.COMPLETED


async def test_claude_reports_no_provider_discovery(runtime):
    # The handshake carries no skills collection, only a flat command list
    # that mixes built-ins with skills and has no source field.
    backend, call = runtime.backend(), runtime.call()

    assert await backend.discover_skills(call) == ()
    with pytest.raises(HarnessOperationNotSupportedError):
        await backend.discover_mcp(call)


async def test_turn_projects_qwenpaw_skills_and_mcp(runtime, tmp_path):
    skill_dir = tmp_path / "workspace-skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\n",
        encoding="utf-8",
    )
    capabilities = HarnessRuntimeCapabilities(
        skills=(
            HarnessSkillDefinition(
                name="review",
                description="Review code",
                directory=skill_dir,
            ),
        ),
        mcp_servers=(
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="streamable_http",
                url="https://mcp.example.test",
                headers={"Authorization": "Bearer secret"},
                tools=("search", "index", "purge"),
                tool_policies={
                    "search": "allow",
                    "index": "ask",
                    "purge": "deny",
                },
            ),
        ),
    )

    await collect(
        runtime.backend(),
        runtime.call(capabilities=capabilities),
        "Review it",
    )

    options = runtime.clients[0].options
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
    assert plugin_path.is_relative_to(runtime.context.state_dir)
    assert (plugin_path / "skills" / "review" / "SKILL.md").is_file()


async def test_turn_sends_images_and_files_through_the_sdk_protocol(runtime):
    call = runtime.call()
    image = call.cwd / "media" / "mockup.png"
    image.parent.mkdir()
    image.write_bytes(b"image-bytes")
    document = call.cwd / "media" / "product brief.pdf"
    document.write_bytes(b"document-bytes")

    await collect(
        runtime.backend(),
        call,
        "Review both attachments",
        (
            HarnessAttachment(HarnessAttachmentKind.IMAGE, image, image.name),
            HarnessAttachment(
                HarnessAttachmentKind.FILE,
                document,
                document.name,
            ),
        ),
    )

    messages, query_session_id = runtime.clients[0].prompts[0]
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


# Approvals


def _permission(**overrides) -> ToolPermissionContext:
    values = {
        "description": "Run tests",
        "display_name": "Shell",
        "tool_use_id": "tool-1",
    }
    values.update(overrides)
    return ToolPermissionContext(**values)


@pytest.mark.parametrize(
    ("decision", "behavior"),
    [("approved", "allow"), ("rejected", "deny"), ("timeout", "deny")],
)
async def test_approval_belongs_to_the_live_call(runtime, decision, behavior):
    seen = []

    async def decide(summary, *, timeout):
        seen.append((summary, timeout))
        return decision

    backend = runtime.backend()
    backend._contexts[_IDENTITY] = runtime.call(_request_approval=decide)

    result = await backend._approve_tool(
        _IDENTITY,
        "Bash",
        {"command": "pytest"},
        _permission(),
    )

    assert result.behavior == behavior
    ((summary, timeout),) = seen
    assert timeout == adapter_module._APPROVAL_TIMEOUT_SECONDS
    assert (summary.tool_name, summary.title, summary.severity) == (
        "Bash",
        "Shell",
        "high",
    )
    assert summary.description == "Run tests"
    assert summary.parameters == {
        "provider": "claude",
        "provider_item_id": "tool-1",
        "tool_name": "Bash",
        "input": {"command": "pytest"},
        "blocked_path": None,
    }
    assert not backend._approval_tasks[_IDENTITY]


async def test_approval_without_a_live_call_is_denied(runtime):
    result = await runtime.backend()._approve_tool(
        _IDENTITY,
        "Write",
        {"path": "/etc/hosts"},
        _permission(blocked_path="/etc/hosts"),
    )

    assert isinstance(result, PermissionResultDeny)


# History, reset and cancellation


async def test_history_uses_the_persisted_claude_session(runtime):
    runtime.sessions.write_text(json.dumps({"chat": _NATIVE}))
    messages = [
        SessionMessage(
            type="assistant",
            uuid="assistant-1",
            session_id=_NATIVE,
            message={"content": [{"type": "text", "text": "Recovered"}]},
        ),
    ]
    backend, call = runtime.backend(), runtime.call()

    with patch.object(
        adapter_module,
        "get_session_messages",
        return_value=messages,
    ) as get_messages:
        history = await backend.history(call)

    assert [item.text for item in history] == ["Recovered"]
    get_messages.assert_called_once_with(_NATIVE, str(call.cwd))
    # Reading claims the legacy session-ID key for this user and channel.
    assert json.loads(runtime.sessions.read_text()) == {_IDENTITY: _NATIVE}
    assert await backend.history(runtime.call("bob")) == ()


async def test_reset_session_forgets_the_local_mapping(runtime):
    backend, call = runtime.backend(), runtime.call()
    await collect(backend, call)

    await backend.reset_session(call)

    assert backend._sessions == {}
    assert backend._clients == {}
    assert backend._readers == {}
    assert json.loads(runtime.sessions.read_text()) == {}
    assert runtime.clients[0].disconnected is True
    await collect(backend, call)
    assert runtime.clients[-1].options.resume is None


async def test_failed_disconnect_still_forgets_the_mapping(runtime):
    backend, call = runtime.backend(), runtime.call()
    await collect(backend, call)

    async def broken() -> None:
        raise CLIConnectionError("gone")

    runtime.clients[0].disconnect = broken
    with pytest.raises(SDKError):
        await backend.reset_session(call)

    assert json.loads(runtime.sessions.read_text()) == {}
    assert backend._clients == {}


async def test_cancelling_a_turn_interrupts_claude(runtime):
    response_started = asyncio.Event()

    class SilentClaudeClient(FakeClaudeClient):
        async def query(self, prompt: Any, session_id: str = "default"):
            self.prompts.append((prompt, session_id))
            response_started.set()

    client = SilentClaudeClient(ClaudeAgentOptions())
    backend = runtime.backend(client_factory=lambda _options: client)

    turn = asyncio.create_task(collect(backend, runtime.call(), "Wait"))
    await response_started.wait()
    turn.cancel()

    with pytest.raises(asyncio.CancelledError):
        await turn

    assert client.interrupted is True
    assert client.disconnected is True
    assert not backend._contexts


@pytest.mark.parametrize("cleanup_failure", [None, "interrupt", "disconnect"])
async def test_cancelled_turn_reconnects_without_stale_result(
    runtime,
    cleanup_failure,
):
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

    backend, call = runtime.backend(client_factory=factory), runtime.call()

    async def consume():
        events = []
        async for event in backend.run_turn(call, HarnessRunRequest("Hi")):
            events.append(event)
            started.set()
        return events

    task = asyncio.create_task(consume())
    await started.wait()
    resume_id = backend._sessions[_IDENTITY]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(5):
        events = await consume()
    assert any(event.text == "Done" for event in events)
    assert len(clients) == 2
    assert clients[0].disconnected
    assert clients[1].options.resume == resume_id


async def test_closing_turn_at_yield_evicts_client_and_resumes(runtime):
    backend, call = runtime.backend(), runtime.call()
    stream = backend.run_turn(call, HarnessRunRequest("Hi"))
    assert (await anext(stream)).text == "Done"
    resume_id = backend._sessions[_IDENTITY]
    await stream.aclose()

    first = runtime.clients[0]
    assert first.interrupted and first.disconnected
    assert _IDENTITY not in backend._clients
    events = await collect(backend, call, "Again")
    assert any(event.text == "Done" for event in events)
    assert runtime.clients[1].options.resume == resume_id


async def test_closing_reset_notice_evicts_fresh_client(runtime):
    runtime.sessions.write_text(json.dumps({_IDENTITY: "stale"}))
    clients = []
    backend = runtime.backend(client_factory=_refusing_factory(clients))
    stream = backend.run_turn(runtime.call(), HarnessRunRequest("Hi"))

    assert "no longer exists" in (await anext(stream)).text
    fresh = clients[-1]
    assert not fresh.prompts
    await stream.aclose()

    assert fresh.interrupted and fresh.disconnected
    assert _IDENTITY not in backend._clients
    assert _IDENTITY not in backend._pending
    assert backend._sessions == {}


async def test_close_disconnects_every_owned_client(runtime):
    backend = runtime.backend()
    await collect(backend, runtime.call())
    await collect(backend, runtime.call("bob"))

    await backend.close()

    assert all(client.disconnected for client in runtime.clients)
    assert not backend._owned_clients and not backend._readers
    with pytest.raises(SDKError, match="closed"):
        await backend.prepare()


# MCP policy gate


def _gate_options(runtime, servers, permission_mode="default"):
    backend = runtime.backend()
    call = runtime.call(
        settings={"permission_mode": permission_mode},
        capabilities=HarnessRuntimeCapabilities(mcp_servers=tuple(servers)),
    )
    options = backend._options(call, "id", False, None, runtime.binary)
    return backend, options


@pytest.mark.parametrize("default_policy", ["allow", "ask", "deny"])
async def test_empty_mcp_allowlist_is_not_connected(runtime, default_policy):
    _, options = _gate_options(
        runtime,
        [
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                tools=(),
                default_policy=default_policy,
            ),
        ],
    )
    assert options.mcp_servers == {}


@pytest.mark.parametrize("mode", ["default", "bypassPermissions"])
@pytest.mark.parametrize(
    "tools,default,policies,tool,effect",
    [
        (("read",), "allow", {}, "write", "deny"),
        (("read",), "allow", {}, "read", "allow"),
        (("read",), "deny", {"read": "allow"}, "read", "allow"),
        (("read",), "allow", {"read": "deny"}, "read", "deny"),
        (None, "allow", {}, "write", "allow"),
        (None, "deny", {}, "write", "deny"),
        (None, "ask", {}, "read", "ask"),
        (("read",), "allow", {"read": "ask"}, "read", "ask"),
    ],
)
@pytest.mark.parametrize("approved", [False, True])
async def test_mcp_pre_execution_policy(
    runtime,
    mode,
    tools,
    default,
    policies,
    tool,
    effect,
    approved,
):
    backend, options = _gate_options(
        runtime,
        [
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                tools=tools,
                default_policy=default,
                tool_policies=policies,
            ),
        ],
        mode,
    )
    approval = AsyncMock(
        return_value=(
            PermissionResultAllow() if approved else PermissionResultDeny()
        ),
    )
    backend._approve_tool = approval
    assert options.hooks and options.hooks.get("PreToolUse")
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook(
        {"tool_name": f"mcp__docs__{tool}", "tool_input": {"x": 1}},
        "call-1",
        {},
    )
    expected = ("allow" if approved else "deny") if effect == "ask" else effect
    assert result["hookSpecificOutput"]["permissionDecision"] == expected
    assert approval.await_count == (1 if effect == "ask" else 0)
    assert await hook({"tool_name": "Read"}, "call-2", {}) == {}
    if expected == "allow":
        assert "mcp__docs__*" not in options.disallowed_tools


async def test_mcp_hook_matches_longest_managed_server_name(runtime):
    _, options = _gate_options(
        runtime,
        [
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                default_policy="deny",
            ),
            HarnessMCPServerDefinition(
                name="docs__private",
                display_name="Private",
                transport="stdio",
                command="docs",
                tools=None,
                default_policy="deny",
                tool_policies={"read": "allow"},
            ),
        ],
    )
    assert set(options.mcp_servers) == {"docs__private"}
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook({"tool_name": "mcp__docs__private__read"}, "call", {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert await hook({"tool_name": "mcp__unmanaged__read"}, "call", {}) == {}


async def test_mcp_policy_uses_the_cli_server_name(runtime):
    # Claude Code names tools mcp__<server>__<tool>, with every character of
    # the server name outside [a-zA-Z0-9_-] replaced by "_".
    _, options = _gate_options(
        runtime,
        [
            HarnessMCPServerDefinition(
                name="my docs.v2",
                display_name="Docs",
                transport="stdio",
                command="docs",
                tools=None,
                default_policy="allow",
                tool_policies={"purge": "deny"},
            ),
        ],
        "bypassPermissions",
    )
    assert options.disallowed_tools == ["mcp__my_docs_v2__purge"]
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook({"tool_name": "mcp__my_docs_v2__purge"}, "call", {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_mcp_approval_failure_returns_explicit_deny(
    runtime,
    monkeypatch,
    failure,
):
    monkeypatch.setattr(adapter_module, "_APPROVAL_TIMEOUT_SECONDS", 0.01)
    backend, options = _gate_options(
        runtime,
        [
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                default_policy="ask",
            ),
        ],
        "bypassPermissions",
    )

    async def broken_approval(*args):
        if failure == "error":
            raise RuntimeError("approval service unavailable")
        await asyncio.Event().wait()

    backend._approve_tool = broken_approval
    hook = options.hooks["PreToolUse"][0].hooks[0]
    async with asyncio.timeout(1):
        result = await hook({"tool_name": "mcp__docs__read"}, "call", {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_mcp_approval_cancellation_propagates(runtime):
    backend = runtime.backend()
    capabilities = HarnessRuntimeCapabilities(
        mcp_servers=(
            HarnessMCPServerDefinition(
                name="docs",
                display_name="Docs",
                transport="stdio",
                command="docs",
                default_policy="ask",
            ),
        ),
    )
    started = asyncio.Event()

    async def pending_approval(*args):
        started.set()
        await asyncio.Event().wait()

    backend._approve_tool = pending_approval
    task = asyncio.create_task(
        backend._gate_mcp_tool(
            _IDENTITY,
            capabilities,
            {"tool_name": "mcp__docs__read"},
            "call",
        ),
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# Continuous session reader


async def test_messages_between_turns_are_drained_not_replayed(runtime):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    backend = runtime.backend(client_factory=lambda _options: client)
    call = runtime.call()

    assert _texts(await collect(backend, call, "one")) == ["First"]

    # A background agent wakes the session and finishes a whole CLI turn,
    # with more output than the SDK buffers, while no QwenPaw turn runs.
    for message in [
        SystemMessage(subtype="init", data={}),
        *[_text_delta("Background") for _ in range(150)],
        _result(),
    ]:
        client.inbox.put_nowait(message)
    await _drained(client)

    client.messages = _turn("Second")
    assert _texts(await collect(backend, call, "two")) == ["Second"]


async def test_prompt_during_background_turn_skips_that_turn(runtime):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    backend = runtime.backend(client_factory=lambda _options: client)
    call = runtime.call()
    await collect(backend, call, "one")

    # The wake-up turn is still open when the next prompt is sent; the CLI
    # finishes it before it runs the prompt.
    client.inbox.put_nowait(SystemMessage(subtype="init", data={}))
    client.inbox.put_nowait(_text_delta("Background"))
    await _drained(client)
    client.messages = [
        _text_delta("More background"),
        _result(),
        *_turn("Second"),
    ]

    events = await collect(backend, call, "two")

    assert _texts(events) == ["Second"]
    assert events[-1].kind == HarnessEventKind.COMPLETED


async def test_prompt_folded_into_background_turn_ends_with_it(runtime):
    client = FakeClaudeClient(ClaudeAgentOptions())
    client.messages = _turn("First")
    backend = runtime.backend(client_factory=lambda _options: client)
    call = runtime.call()
    await collect(backend, call, "one")

    # A wake-up turn is running a tool when the prompt arrives; the CLI
    # appends the prompt to that turn after the tool result.
    client.inbox.put_nowait(SystemMessage(subtype="init", data={}))
    client.inbox.put_nowait(_text_delta("Background"))
    await _drained(client)
    client.messages = [
        UserMessage(content=[ToolResultBlock(tool_use_id="toolu_bg")]),
        UserMessage(content="two", uuid="echo-two"),
        _text_delta("Second"),
        _result(),
    ]

    events = await collect(backend, call, "two")

    assert _texts(events) == ["Second"]
    assert events[-1].kind == HarnessEventKind.COMPLETED


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
            self.frames.put_nowait(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": message["request_id"],
                        "response": {},
                    },
                },
            )
        elif message.get("type") == "user":
            for frame in (
                {"type": "system", "subtype": "init"},
                {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 1,
                    "duration_api_ms": 1,
                    "is_error": False,
                    "num_turns": 1,
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


async def test_background_permission_request_is_answered_after_turn(runtime):
    transport = _ScriptedTransport()
    backend = runtime.backend(
        client_factory=lambda options: ClaudeSDKClient(
            options,
            transport=transport,
        ),
    )
    await collect(backend, runtime.call(), "Start")

    # A background agent keeps streaming past the SDK's 100-message buffer,
    # then asks to run a tool, with no QwenPaw turn running.
    for index in range(150):
        transport.frames.put_nowait(
            {
                "type": "stream_event",
                "uuid": f"bg-{index}",
                "session_id": "claude-session",
                "event": {"type": "content_block_delta"},
                "parent_tool_use_id": "toolu_background",
            },
        )
    transport.frames.put_nowait(
        {
            "type": "control_request",
            "request_id": "perm-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "ls"},
                "permission_suggestions": None,
            },
        },
    )

    async with asyncio.timeout(5):
        while not any(
            response.get("request_id") == "perm-1"
            for response in transport.control_responses
        ):
            await asyncio.sleep(0.01)
    (answer,) = [
        response
        for response in transport.control_responses
        if response.get("request_id") == "perm-1"
    ]
    # No live QwenPaw call can route the request, so it is denied promptly
    # instead of stalling the CLI.
    assert answer["response"]["behavior"] == "deny"
