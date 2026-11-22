# -*- coding: utf-8 -*-
"""Antigravity backend scenarios against a scripted fake ACP server."""

# pylint: disable=protected-access,redefined-outer-name

import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.plugins import SDKError
from qwenpaw.plugins.harnesses import (
    HarnessAttachment,
    HarnessAttachmentKind,
    HarnessCallContext,
    HarnessContext,
    HarnessEventKind as Kind,
    HarnessHistoryKind as History,
    HarnessOperationNotSupportedError,
    HarnessRunRequest,
)
from qwenpaw.plugins.resources import ResourceOwner, ResourceScope
from qwenpaw.plugins.storage import PluginStorage
from qwenpaw.plugins.testing import PluginTestHost
from plugins.harness.antigravity import adapter as adapter_module
from plugins.harness.antigravity import auth, discovery
from plugins.harness.antigravity.acp import AcpClient
from plugins.harness.antigravity.adapter import (
    AntigravityBackend,
    validate_settings,
)
from plugins.harness.antigravity.event_mapper import (
    EventMapper,
    history_items,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Antigravity CLI is supported on Linux only",
)

PLUGIN_ID = "qwenpaw-harness-antigravity"
SESSIONS = "antigravity_sessions.json"
FAKE = Path(__file__).with_name("fake_agy.py")
KEY = json.dumps(["alice", "console", "one"])


def fake_cli(directory):
    """An executable ``agy`` that runs the fake ACP server."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "agy"
    binary.write_text(
        f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n',
    )
    binary.chmod(0o755)
    return str(binary)


@pytest.fixture
def cli(tmp_path):
    return fake_cli(tmp_path / "bin")


@pytest.fixture(name="runtime")
async def make_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    state = workspace / "plugin-data" / PLUGIN_ID
    state.mkdir(parents=True)
    fake_state = tmp_path / "fake"
    fake_state.mkdir()
    owner = ResourceOwner(PLUGIN_ID, 1, "workspace")
    context = HarnessContext(
        PLUGIN_ID,
        1,
        "workspace",
        workspace,
        state,
        PluginStorage(state, lambda: None),
        ResourceScope(owner, lambda: None),
        SimpleNamespace(),
    )
    backends = []

    def backend(binary=None, **env):
        value = AntigravityBackend(
            context,
            binary=binary,
            env={"FAKE_ACP_STATE": str(fake_state), **env},
        )
        backends.append(value)
        return value

    def call(
        user="alice",
        session="one",
        settings=None,
        cwd=None,
        approval=None,
    ):
        return HarnessCallContext(
            f"{user}-{session}",
            session,
            user,
            "console",
            cwd or workspace,
            dict(settings or {}),
            None,
            context.resources.child(instance_id="call"),
            _request_approval=approval,
        )

    def stored():
        return json.loads((state / SESSIONS).read_text(encoding="utf-8"))

    def log(method=None):
        path = fake_state / "log.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [row for row in rows if method in (None, row.get("method"))]

    yield SimpleNamespace(
        context=context,
        workspace=workspace,
        state=state,
        fake_state=fake_state,
        backend=backend,
        call=call,
        stored=stored,
        log=log,
    )
    for value in backends:
        await value.close()


async def turn(backend, call, prompt="hello", attachments=()):
    request = HarnessRunRequest(prompt, attachments)
    async with asyncio.timeout(20):
        return [event async for event in backend.run_turn(call, request)]


async def eventually(check, seconds=5):
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.02)


def texts(events, kind=Kind.TEXT_DELTA):
    return "".join(event.text for event in events if event.kind == kind)


def test_spec_declares_only_supported_capabilities():
    from plugins.harness.antigravity.main import plugin

    specs = []
    plugin.register(SimpleNamespace(register_harness=specs.append))
    (spec,) = specs
    assert (spec.id, spec.label, spec.instance_settings_keys) == (
        "antigravity",
        "Antigravity",
        ("binary",),
    )
    capabilities = spec.capabilities
    assert capabilities.model_selection
    assert capabilities.reasoning_effort
    assert capabilities.reasoning_stream
    assert capabilities.tool_stream
    assert capabilities.session_resume
    assert not capabilities.authentication
    assert not capabilities.attachments
    assert not capabilities.qwenpaw_mcp_projection
    assert not capabilities.qwenpaw_skills_projection
    assert not capabilities.mcp_tool_allowlist
    assert not capabilities.external_history
    assert capabilities.commands == ()
    assert [p.settings for p in capabilities.approval_presets] == [
        {"permission_mode": "configured"},
        {"permission_mode": "accept-edits"},
        {"permission_mode": "plan"},
        {"permission_mode": "full-access"},
    ]


# Prompt streaming and session lifecycle


async def test_prompt_streams_text_reasoning_and_usage(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    first = await turn(backend, call)
    assert [event.kind for event in first] == [
        Kind.REASONING_DELTA,
        Kind.TEXT_DELTA,
        Kind.COMPLETED,
    ]
    assert texts(first, Kind.REASONING_DELTA) == "thinking"
    assert first[-1].data == {
        "stop_reason": "end_turn",
        "usage": {"inputTokens": 3, "outputTokens": 4},
        "usage_scope": "turn",
        "context_window": {"used": 7, "size": 100},
    }
    second = await turn(backend, call)
    # One long-lived server and one ACP session serve both turns.
    assert texts(first) == texts(second)
    assert "sid:session-1" in texts(first)
    assert len(runtime.log("initialize")) == 1
    assert len(runtime.log("session/new")) == 1
    assert runtime.stored() == {
        "version": 1,
        "sessions": {
            KEY: {
                "conversation_id": "session-1",
                "cwd": str(runtime.workspace.resolve()),
            },
        },
    }


async def test_resume_in_fresh_process_keeps_conversation(runtime, cli):
    backend = runtime.backend(cli)
    first = await turn(backend, runtime.call())
    await backend.close()
    second = await turn(runtime.backend(cli), runtime.call())
    assert second[-1].kind == Kind.COMPLETED
    assert "sid:session-1" in texts(second)
    assert texts(first) != texts(second)  # a new pid
    (resume,) = runtime.log("session/resume")
    assert resume["params"]["sessionId"] == "session-1"
    assert len(runtime.log("session/new")) == 1


async def test_independent_identities_share_one_server(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    first, second = await asyncio.gather(
        turn(backend, call),
        turn(backend, call),
    )
    assert texts(first) == texts(second)
    other_session = await turn(backend, runtime.call(session="two"))
    other_user = await turn(backend, runtime.call(user="bob"))
    sessions = {
        texts(events).split("sid:")[1]
        for events in (first, other_session, other_user)
    }
    assert len(sessions) == 3
    assert len(runtime.log("initialize")) == 1
    assert set(runtime.stored()["sessions"]) == {
        KEY,
        json.dumps(["alice", "console", "two"]),
        json.dumps(["bob", "console", "one"]),
    }


async def test_cwd_requires_explicit_reset(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    other = runtime.workspace / "other"
    other.mkdir()
    events = await turn(backend, runtime.call(cwd=other))
    assert events[-1].kind == Kind.ERROR
    assert "clear" in events[-1].text.lower()
    await backend.reset_session(runtime.call(cwd=other))
    events = await turn(backend, runtime.call(cwd=other))
    assert events[-1].kind == Kind.COMPLETED
    assert runtime.stored()["sessions"][KEY]["cwd"] == str(other.resolve())


async def test_reset_closes_session_and_stops_idle_server(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    process = backend._client.process
    await backend.reset_session(runtime.call())
    assert [row["params"] for row in runtime.log("session/close")] == [
        {"sessionId": "session-1"},
    ]
    assert KEY not in runtime.stored()["sessions"]
    assert backend._client is None and process.returncode is not None
    events = await turn(backend, runtime.call())
    assert "sid:session-2" in texts(events)


async def test_idle_expiry_closes_session_and_server(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    assert KEY in backend._idle
    process = backend._client.process
    backend._cancel_idle(KEY)
    await backend._expire(KEY, 0)
    assert runtime.log("session/close")
    assert backend._client is None and process.returncode is not None
    # The mapping survives; the next turn resumes it.
    events = await turn(backend, runtime.call())
    assert "sid:session-1" in texts(events)
    assert runtime.log("session/resume")


async def test_close_reaps_process_group(runtime, cli):
    backend = runtime.backend(cli)
    events = await turn(backend, runtime.call(), "child")
    assert texts(events) == "spawned"
    child = int((runtime.fake_state / "child.pid").read_text())
    process = backend._client.process
    await backend.close()
    assert process.returncode is not None
    async with asyncio.timeout(5):
        while True:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
    events = await turn(backend, runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]
    assert "closed" in events[0].text


async def test_resume_failure_keeps_mapping(runtime, cli):
    record = {
        "conversation_id": "gone",
        "cwd": str(runtime.workspace.resolve()),
    }
    (runtime.state / SESSIONS).write_text(
        json.dumps({"version": 1, "sessions": {KEY: record}}),
    )
    events = await turn(runtime.backend(cli), runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]
    assert "Could not resume" in events[0].text
    assert runtime.stored()["sessions"][KEY] == record


async def test_authentication_required_explains_login(runtime, cli):
    backend = runtime.backend(cli, FAKE_ACP_NOAUTH="1")
    events = await turn(backend, runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]
    assert "requires login" in events[0].text
    status = await backend.status(runtime.call())
    assert status.available and not status.authenticated
    assert status.account["authentication_status"] == "unauthenticated"


@pytest.mark.parametrize(
    "settings",
    [
        {"print_timeout_seconds": 0},
        {"print_timeout_seconds": True},
        {"idle_timeout_seconds": 29},
        {"permission_mode": "unsafe"},
        {"reasoning_effort": 3},
        {"model": 3},
    ],
)
async def test_rejects_invalid_settings(runtime, cli, settings):
    backend = runtime.backend(cli)
    events = await turn(backend, runtime.call(settings=settings))
    assert [event.kind for event in events] == [Kind.ERROR]
    assert backend._client is None


async def test_rejects_attachments_and_slash_prompts(runtime, cli, tmp_path):
    backend = runtime.backend(cli)
    attachment = HarnessAttachment(HarnessAttachmentKind.FILE, tmp_path)
    events = await turn(backend, runtime.call(), attachments=(attachment,))
    assert "attachments" in events[-1].text
    events = await turn(backend, runtime.call(), " /compact")
    assert "slash commands" in events[-1].text
    assert backend._client is None


async def test_mode_model_and_effort_apply_once(runtime, cli):
    def modes():
        rows = runtime.log("session/set_mode")
        return [row["params"]["modeId"] for row in rows]

    backend = runtime.backend(cli)
    settings = {"permission_mode": "plan", "model": "model-a-low"}
    for _ in range(2):
        events = await turn(backend, runtime.call(settings=settings))
        assert events[-1].kind == Kind.COMPLETED
    assert modes() == ["plan"]
    assert [
        row["params"]["modelId"] for row in runtime.log("session/set_model")
    ] == ["model-a-low"]
    # A listed id names its effort; only the default model takes one.
    await turn(
        backend,
        runtime.call(settings={"model": "default", "reasoning_effort": "low"}),
    )
    (option,) = runtime.log("session/set_config_option")
    assert option["params"]["configId"] == "thought_level"
    assert option["params"]["value"] == "low"
    assert modes() == ["plan", "default"]


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"model": "unknown-model"}, "does not offer model"),
        ({"reasoning_effort": "bogus"}, "not offered"),
    ],
)
async def test_unknown_model_or_effort_is_error(
    runtime,
    cli,
    settings,
    message,
):
    events = await turn(runtime.backend(cli), runtime.call(settings=settings))
    assert [event.kind for event in events] == [Kind.ERROR]
    assert message in events[0].text


async def test_malformed_lines_are_skipped(runtime, cli):
    events = await turn(runtime.backend(cli), runtime.call(), "garbage")
    assert texts(events) == "survived"
    assert events[-1].kind == Kind.COMPLETED


async def test_server_crash_is_error_and_next_turn_restarts(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    events = await turn(backend, runtime.call(), "crash")
    assert events[-1].kind == Kind.ERROR
    assert "boom" in events[-1].text and "SECRET" not in events[-1].text
    assert runtime.stored()["sessions"][KEY]["conversation_id"] == "session-1"
    events = await turn(backend, runtime.call())
    assert events[-1].kind == Kind.COMPLETED
    assert len(runtime.log("initialize")) == 2
    assert runtime.log("session/resume")


async def test_refusal_is_an_error(runtime, cli):
    events = await turn(runtime.backend(cli), runtime.call(), "refuse")
    assert events[-1].kind == Kind.ERROR
    assert "refusal" in events[-1].text


# Permissions


@pytest.mark.parametrize(
    "decision, outcome",
    [
        ("approved", "allow"),
        ("rejected", "deny"),
        ("timeout", "deny"),
        ("cancelled", "cancelled"),
    ],
)
async def test_permission_request_uses_host_approval(
    runtime,
    cli,
    decision,
    outcome,
):
    summaries = []

    async def approval(summary, *, timeout):
        summaries.append((summary, timeout))
        return decision

    events = await turn(
        runtime.backend(cli),
        runtime.call(approval=approval),
        "tool",
    )
    (summary, timeout), = summaries
    assert timeout == 300
    assert (summary.tool_name, summary.title, summary.severity) == (
        "run_command",
        "echo hi",
        "high",
    )
    assert summary.parameters == {
        "provider": "antigravity",
        "provider_item_id": "call-1",
        "tool_name": "run_command",
        "kind": "execute",
        "input": {"CommandLine": "echo hi"},
    }
    assert texts(events) == f"outcome:{outcome}"
    tools = [event for event in events if event.kind.value.startswith("tool")]
    assert [event.kind for event in tools] == [
        Kind.TOOL_STARTED,
        Kind.TOOL_COMPLETED,
    ]
    assert tools[0].data["arguments"] == {"CommandLine": "echo hi"}
    assert tools[1].text == ("hi\n" if outcome == "allow" else "")


async def test_full_access_auto_approves_without_dialog(runtime, cli):
    async def approval(summary, *, timeout):
        raise AssertionError("full access must not ask")

    events = await turn(
        runtime.backend(cli),
        runtime.call(
            settings={"permission_mode": "full-access"},
            approval=approval,
        ),
        "tool",
    )
    assert texts(events) == "outcome:allow"


async def test_unavailable_approval_rejects(runtime, cli):
    events = await turn(runtime.backend(cli), runtime.call(), "tool")
    assert texts(events) == "outcome:deny"


async def test_permission_outside_a_turn_is_rejected(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    options = [
        {"optionId": "yes", "kind": "allow_always"},
        {"optionId": "no", "kind": "reject_always"},
    ]
    params = {"sessionId": "session-1", "options": options}
    reply = await backend._on_request("session/request_permission", params)
    assert reply == {"outcome": {"outcome": "selected", "optionId": "no"}}
    params["options"] = options[:1]
    reply = await backend._on_request("session/request_permission", params)
    assert reply == {"outcome": {"outcome": "cancelled"}}
    with pytest.raises(adapter_module.AcpError):
        await backend._on_request("fs/read_text_file", {})


# Cancellation


async def test_aclose_sends_session_cancel_and_session_stays_usable(
    runtime,
    cli,
):
    backend = runtime.backend(cli)
    stream = backend.run_turn(runtime.call(), HarnessRunRequest("slow"))
    first = await asyncio.wait_for(stream.__anext__(), 10)
    assert first.text == "working"
    await asyncio.wait_for(stream.aclose(), 10)
    (cancel,) = runtime.log("session/cancel")
    assert cancel["params"] == {"sessionId": "session-1"}
    events = await turn(backend, runtime.call())
    assert events[-1].kind == Kind.COMPLETED
    assert len(runtime.log("initialize")) == 1


async def test_task_cancel_waiting_on_approval_answers_cancelled(
    runtime,
    cli,
):
    asked = asyncio.Event()

    async def approval(summary, *, timeout):
        asked.set()
        await asyncio.sleep(60)

    backend = runtime.backend(cli)
    task = asyncio.create_task(
        turn(backend, runtime.call(approval=approval), "tool"),
    )
    await asyncio.wait_for(asked.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = [
        row
        for row in runtime.log()
        if row.get("id") == "perm-1" and "result" in row
    ]
    assert replies[0]["result"] == {"outcome": {"outcome": "cancelled"}}
    await eventually(lambda: runtime.log("session/cancel"))
    assert not backend._approval_tasks.get(KEY)


async def test_timeout_cancels_the_prompt(runtime, cli):
    events = await turn(
        runtime.backend(cli),
        runtime.call(settings={"print_timeout_seconds": 1}),
        "slow",
    )
    assert events[-1].kind == Kind.ERROR
    assert "timed out" in events[-1].text
    assert runtime.log("session/cancel")


async def test_unsettled_cancel_drops_the_live_session(
    runtime,
    cli,
    monkeypatch,
):
    monkeypatch.setattr(adapter_module, "_CANCEL_GRACE_SECONDS", 0.2)
    backend = runtime.backend(cli)
    stream = backend.run_turn(
        runtime.call(),
        HarnessRunRequest("ignore-cancel"),
    )
    await asyncio.wait_for(stream.__anext__(), 10)
    await asyncio.wait_for(stream.aclose(), 5)
    assert KEY not in backend._live
    assert runtime.stored()["sessions"][KEY]["conversation_id"] == "session-1"


# Models and history


async def test_models_come_from_a_reused_catalog_session(runtime, cli):
    backend = runtime.backend(cli)
    models = await backend.models(runtime.call())
    assert [(model.id, model.is_default) for model in models] == [
        ("default", True),
        ("model-a-high", False),
        ("model-a-low", False),
    ]
    assert models[0].reasoning_efforts == ("low", "high")
    assert await backend.models(runtime.call()) is models
    assert backend._client is None  # nothing stays loaded
    await backend.close()
    await runtime.backend(cli).models(runtime.call())
    # The second backend resumes the stored catalog session.
    assert len(runtime.log("session/new")) == 1
    resumed = [row["params"] for row in runtime.log("session/resume")]
    assert [params["sessionId"] for params in resumed] == ["session-1"]
    assert len(runtime.log("session/close")) == 2


async def test_models_failure_explains_itself(runtime, cli):
    backend = runtime.backend(cli, FAKE_ACP_NOAUTH="1")
    (model,) = await backend.models(runtime.call())
    assert model.id == "default" and "requires login" in model.description


async def test_history_replays_session_load(runtime, cli):
    backend = runtime.backend(cli)
    assert await backend.history(runtime.call()) == ()
    await turn(backend, runtime.call())
    await backend.close()
    backend = runtime.backend(cli)
    history = await backend.history(runtime.call())
    assert [(item.kind, item.text) for item in history] == [
        (History.USER, "hello"),
        (History.MESSAGE, "reply to hello"),
    ]
    # The loaded session is live: the next turn needs no resume.
    events = await turn(backend, runtime.call())
    assert events[-1].kind == Kind.COMPLETED
    assert not runtime.log("session/resume")


# State


async def test_legacy_store_is_copied_once_and_claimed(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call(session="seed"))
    await backend.close()
    legacy = runtime.workspace / "harnesses" / SESSIONS
    legacy.parent.mkdir()
    record = {
        "conversation_id": "session-1",
        "cwd": str(runtime.workspace.resolve()),
        "settings_fingerprint": "old",
        "last_usage": {"total_tokens": 5},
    }
    original = json.dumps({"version": 1, "sessions": {"one": record}})
    legacy.write_text(original)
    (runtime.state / SESSIONS).unlink()
    backend = runtime.backend(cli)
    # An old stream-json conversation id is the ACP session id.
    events = await turn(backend, runtime.call())
    assert "sid:session-1" in texts(events)
    sessions = runtime.stored()["sessions"]
    assert list(sessions) == [KEY]
    assert sessions[KEY]["conversation_id"] == "session-1"
    await turn(backend, runtime.call(user="bob"))
    bob = runtime.stored()["sessions"][json.dumps(["bob", "console", "one"])]
    assert bob["conversation_id"] == "session-2"
    assert legacy.read_text() == original
    await backend.reset_session(runtime.call())
    assert KEY not in runtime.stored()["sessions"]
    assert legacy.read_text() == original


@pytest.mark.parametrize("where", ["target", "legacy"])
@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"version": 2, "sessions": {}})],
)
async def test_invalid_state_is_rejected_without_overwrite(
    runtime,
    cli,
    where,
    content,
):
    directory = (
        runtime.state if where == "target" else runtime.workspace / "harnesses"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SESSIONS
    path.write_text(content)
    with pytest.raises(SDKError):
        await runtime.backend(cli).prepare()
    assert path.read_text() == content
    if where == "legacy":
        assert not (runtime.state / SESSIONS).exists()


async def test_unsupported_account_operations(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    with pytest.raises(HarnessOperationNotSupportedError):
        await backend.start_login(call)
    with pytest.raises(HarnessOperationNotSupportedError):
        await backend.logout(call)
    with pytest.raises(HarnessOperationNotSupportedError):
        await backend.run_command(call, "compact", "")
    assert await backend.discover_skills(call) == ()
    assert await backend.discover_mcp(call) == ()


def test_validate_settings_defaults():
    assert validate_settings({}) == {
        "print_timeout_seconds": 3600,
        "idle_timeout_seconds": 600,
        "permission_mode": "configured",
        "effort": None,
        "model": None,
    }


# Discovery


@pytest.fixture
def discovery_env(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    home = tmp_path / "home"
    home.mkdir()
    path = tmp_path / "bin"
    path.mkdir()
    return {"HOME": str(home), "PATH": str(path)}


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_user_local_fallback(discovery_env):
    binary = executable(Path(discovery_env["HOME"]) / ".local/bin/agy")
    status = discovery.discover(env=discovery_env)
    assert status.installed and status.available
    assert status.runtime_path == str(binary)
    assert status.runtime_source == "user-local"


def test_path_precedes_user_local(discovery_env):
    executable(Path(discovery_env["HOME"]) / ".local/bin/agy")
    binary = executable(Path(discovery_env["PATH"]) / "agy")
    status = discovery.discover(env=discovery_env)
    assert status.runtime_path == str(binary)
    assert status.runtime_source == "PATH"


@pytest.mark.parametrize("configured", [None, "agy"])
def test_missing_path_uses_default_not_shell(
    discovery_env,
    monkeypatch,
    configured,
):
    executable(Path(discovery_env["PATH"]) / "agy")
    monkeypatch.setenv("PATH", discovery_env["PATH"])
    default_dir = Path(discovery_env["HOME"]) / "default-bin"
    binary = executable(default_dir / "agy")
    monkeypatch.setattr("os.defpath", str(default_dir))
    status = discovery.discover(
        configured,
        env={"HOME": discovery_env["HOME"]},
    )
    assert status.runtime_path == str(binary)


def test_configured_tilde_uses_supplied_home(discovery_env, monkeypatch):
    monkeypatch.setenv("HOME", "/unrelated-home")
    executable(Path(discovery_env["PATH"]) / "agy")
    executable(Path(discovery_env["HOME"]) / ".local/bin/agy")
    binary = executable(Path(discovery_env["HOME"]) / "custom/agy")
    status = discovery.discover("~/custom/agy", env=discovery_env)
    assert status.runtime_path == str(binary)
    assert status.runtime_source == "configured"


def test_configured_symlink_precedes_path(discovery_env):
    home = Path(discovery_env["HOME"])
    executable(Path(discovery_env["PATH"]) / "agy")
    executable(home / ".local/bin/agy")
    binary = home / "configured"
    binary.symlink_to(executable(home / "target"))
    status = discovery.discover(str(binary), env=discovery_env)
    assert status.available
    assert status.runtime_path == str(binary)
    assert status.runtime_source == "configured"


@pytest.mark.parametrize("kind", ["missing", "nonexec", "directory", "broken"])
def test_invalid_configured_binary_has_no_fallback(discovery_env, kind):
    home = Path(discovery_env["HOME"])
    executable(Path(discovery_env["PATH"]) / "agy")
    executable(home / ".local/bin/agy")
    binary = home / "configured"
    if kind == "nonexec":
        binary.write_text("not executable")
    elif kind == "directory":
        binary.mkdir()
    elif kind == "broken":
        binary.symlink_to(home / "missing-target")
    status = discovery.discover(str(binary), env=discovery_env)
    assert not status.installed
    assert not status.available
    assert status.runtime_path is None
    assert status.runtime_source == "configured"


@pytest.mark.parametrize("kind", ["nonexec", "directory", "broken", "symlink"])
def test_user_local_executable_validation(discovery_env, kind):
    home = Path(discovery_env["HOME"])
    binary = home / ".local/bin/agy"
    binary.parent.mkdir(parents=True)
    if kind == "nonexec":
        binary.write_text("not executable")
    elif kind == "directory":
        binary.mkdir()
    else:
        target = home / "target"
        if kind == "symlink":
            executable(target)
        binary.symlink_to(target)
    status = discovery.discover(env=discovery_env)
    assert status.installed is (kind == "symlink")
    assert status.available is (kind == "symlink")
    if kind == "symlink":
        assert status.runtime_path == str(binary)
        assert status.runtime_source == "user-local"


def test_missing_and_non_linux(monkeypatch, discovery_env):
    missing = str(Path(discovery_env["HOME"]) / "missing")
    assert not discovery.discover(missing, env=discovery_env).installed
    assert "PATH" in discovery.discover(missing, env=discovery_env).error
    executable(Path(discovery_env["HOME"]) / ".local/bin/agy")
    monkeypatch.setattr("sys.platform", "darwin")
    status = discovery.discover(env=discovery_env)
    assert status.installed
    assert not status.available
    assert "Linux" in status.error


# Status and authentication


async def test_status_reports_version_and_authentication(runtime, cli):
    status = await runtime.backend(cli).status(runtime.call())
    assert status.installed and status.available and status.authenticated
    assert status.account == {
        "cli_version": "1.5.14",
        "authentication_status": "authenticated",
    }
    assert status.error is None
    (listing,) = runtime.log("session/list")
    assert listing["params"]["cwd"] != str(runtime.workspace)
    assert not runtime.log("session/new")


async def test_cli_without_acp_is_unavailable(runtime, cli):
    backend = runtime.backend(cli, FAKE_ACP_BROKEN="1")
    status = await backend.status(runtime.call())
    assert status.installed and not status.available
    assert "agy acp" in status.error
    events = await turn(backend, runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]


async def test_version_failure_is_unavailable(runtime, tmp_path):
    binary = tmp_path / "agy"
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    status = await runtime.backend(str(binary)).status(runtime.call())
    assert status.installed and not status.available
    assert "--version" in status.error


async def test_probe_timeout_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "PROBE_TIMEOUT", 0.3)
    binary = tmp_path / "agy"
    binary.write_text(
        '#!/bin/sh\n[ "$1" = --version ] && echo 1.5.14 && exit 0\n'
        "exec sleep 30\n",
    )
    binary.chmod(0o755)
    status = await asyncio.wait_for(
        discovery.Discovery().status(str(binary), dict(os.environ)),
        10,
    )
    assert status.available
    assert status.authentication_status == "unknown"
    assert "timed out" in status.error


async def test_cache_environment_expiry_and_single_probe(
    tmp_path,
    monkeypatch,
):
    calls = []

    async def probe(binary, env):
        del binary
        calls.append(dict(env))
        await asyncio.sleep(0)
        return auth.AuthResult("authenticated")

    monkeypatch.setattr(discovery, "probe_auth", probe)
    cache = discovery.Discovery()
    binary = fake_cli(tmp_path)
    env = dict(os.environ)
    results = await asyncio.gather(
        *(cache.status(binary, env) for _ in range(3)),
    )
    assert len(calls) == 1
    results[0].account.clear()
    assert (await cache.status(binary, env)).account
    await cache.status(binary, {**env, "HOME": "/different"})
    assert len(calls) == 2
    cache._expires = 0
    await cache.status(binary, env)
    assert len(calls) == 3


async def test_unknown_cache_recovers_quickly(tmp_path, monkeypatch):
    async def probe(binary, env):
        del binary, env
        return auth.AuthResult("unknown", "no idea")

    monkeypatch.setattr(discovery, "probe_auth", probe)
    cache = discovery.Discovery()
    await cache.status(fake_cli(tmp_path), dict(os.environ))
    assert 0 < cache._expires - discovery.time.monotonic() <= 5


async def test_client_reports_unsupported_protocol(tmp_path):
    binary = tmp_path / "agy"
    binary.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        "message = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], "
        "'result': {'protocolVersion': 2}}), flush=True)\n"
        "sys.stdin.read()\n",
    )
    binary.chmod(0o755)
    with pytest.raises(adapter_module.AcpError, match="protocol version"):
        await AcpClient.start([str(binary)], tmp_path, dict(os.environ))


# Events


def test_text_reasoning_and_stop_reasons():
    mapper = EventMapper("chat", "turn")
    assert mapper.map(
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "hi"},
        },
    )[0].kind == Kind.TEXT_DELTA
    assert mapper.map(
        {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "hm"},
        },
    )[0].kind == Kind.REASONING_DELTA
    assert not mapper.map(
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "image"}},
    )
    assert not mapper.map({"sessionUpdate": "plan", "entries": []})
    assert [
        mapper.finish({"stopReason": stop})[0].kind
        for stop in ("end_turn", "cancelled", "max_tokens")
    ] == [Kind.COMPLETED, Kind.CANCELLED, Kind.ERROR]


def test_tool_diff_and_duplicate_updates():
    mapper = EventMapper("chat", "turn")
    started = mapper.map(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "t1",
            "name": "edit_file",
            "status": "in_progress",
            "rawInput": {"path": "a.txt"},
        },
    )
    assert [event.kind for event in started] == [Kind.TOOL_STARTED]
    assert started[0].item_id == "chat:turn:t1"
    assert started[0].data["arguments"] == {"path": "a.txt"}
    assert not mapper.map(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "t1",
            "status": "pending",
        },
    )
    done = mapper.map(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "t1",
            "status": "completed",
            "content": [
                {
                    "type": "diff",
                    "path": "a.txt",
                    "oldText": "old\n",
                    "newText": "new\n",
                },
            ],
        },
    )
    assert [event.kind for event in done] == [Kind.TOOL_COMPLETED]
    assert "-old\n+new\n" in done[0].text
    assert not mapper.map(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "t1",
            "status": "completed",
        },
    )
    raw = mapper.map(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "t2",
            "status": "failed",
            "rawOutput": {"output": "nope"},
        },
    )
    assert [event.kind for event in raw] == [
        Kind.TOOL_STARTED,
        Kind.TOOL_COMPLETED,
    ]
    assert raw[1].text == "nope"


def test_history_items_fold_chunks_and_tools():
    def chunk(kind, text):
        return {
            "sessionUpdate": kind,
            "content": {"type": "text", "text": text},
        }

    items = history_items(
        [
            chunk("user_message_chunk", "do "),
            chunk("user_message_chunk", "it"),
            chunk("agent_thought_chunk", "plan"),
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "name": "run_command",
                "status": "completed",
                "rawInput": {"CommandLine": "ls"},
                "content": [
                    {
                        "type": "content",
                        "content": {"type": "text", "text": "a.txt"},
                    },
                ],
            },
            chunk("agent_message_chunk", "do"),
            chunk("agent_message_chunk", "ne"),
            {"sessionUpdate": "usage_update", "used": 1, "size": 2},
        ],
    )
    assert [(item.kind, item.text) for item in items] == [
        (History.USER, "do it"),
        (History.REASONING, "plan"),
        (History.TOOL_CALL, ""),
        (History.TOOL_OUTPUT, "a.txt"),
        (History.MESSAGE, "done"),
    ]
    assert items[2].data == {"arguments": {"CommandLine": "ls"}}
    assert items[3].item_id == "t1"


# Packaging


async def test_actual_zip_manifest_registration_and_host_call(
    tmp_path,
    monkeypatch,
):
    archive = Path(__file__).resolve().parents[4] / (
        f"dist/plugins/harness/{PLUGIN_ID}/{PLUGIN_ID}-1.0.0.zip"
    )
    assert (
        archive.is_file()
    ), "Build and package Harness plugins before vendor tests"
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        zipped.extractall(tmp_path / "unpacked")
    assert not any(name.startswith(f"{PLUGIN_ID}/tests/") for name in names)
    source = tmp_path / "unpacked" / PLUGIN_ID
    manifest = json.loads((source / "plugin.json").read_text())
    assert (
        manifest["id"] == PLUGIN_ID
        and manifest["entry"]["backend"] == "main.py"
    )
    assert (source / manifest["entry"]["frontend"]).is_file()
    bin_dir = tmp_path / "bin"
    fake_cli(bin_dir)
    monkeypatch.setenv("FAKE_ACP_STATE", str(tmp_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    async with PluginTestHost(tmp_path / "host") as host:
        assert (await host.install(source)).ok
        assert [
            (row["id"], row["plugin_id"], row["status"])
            for row in host.list_harnesses()
        ] == [("antigravity", PLUGIN_ID, "available")]
        status = await host.harness_status("antigravity", workspace_id="test")
        assert status.installed and status.runtime_path == str(bin_dir / "agy")
        output = [
            item
            async for item in host.run_harness(
                "antigravity",
                HarnessRunRequest("Review"),
                workspace_id="test",
                session_id="zip-call",
            )
        ]
        assert output[-1].status == "completed"
