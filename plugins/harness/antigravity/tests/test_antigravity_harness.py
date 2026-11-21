# -*- coding: utf-8 -*-
"""Antigravity backend scenarios against inert local agy scripts."""

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
    HarnessOperationNotSupportedError,
    HarnessRunRequest,
)
from qwenpaw.plugins.resources import ResourceOwner, ResourceScope
from qwenpaw.plugins.storage import PluginStorage
from qwenpaw.plugins.testing import PluginTestHost
from plugins.harness.antigravity import auth, discovery
from plugins.harness.antigravity.adapter import (
    AntigravityBackend,
    build_argv,
    validate_settings,
)
from plugins.harness.antigravity.event_mapper import EventMapper
from plugins.harness.antigravity.models import ModelDiscovery

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Antigravity CLI is supported on Linux only",
)

PLUGIN_ID = "qwenpaw-harness-antigravity"
SESSIONS = "antigravity_sessions.json"
FIXTURES = Path(__file__).parent / "fixtures"
KEY = json.dumps(["alice", "console", "one"])
HELP = (
    "--input-format --output-format --conversation --print-timeout "
    "--model --effort --dangerously-skip-permissions"
)
STREAM = (
    "args = sys.argv\n"
    "conversation = args[args.index('--conversation')+1] "
    "if '--conversation' in args else 'conversation-1'\n"
    "for line in sys.stdin:\n"
    "    prompt = json.loads(line)['message']['content']\n"
    "    print(json.dumps({'event': 'init', "
    "'conversation_id': conversation}), flush=True)\n"
    "    if prompt == 'eof': sys.exit(1)\n"
    "    print(json.dumps({'event': 'result', 'status': 'SUCCESS', "
    "'response': str(os.getpid())}), flush=True)\n"
)
ROW = "Gemini Models\tWeekly Limit Remaining\t97%\t2026-09-15T22:05:50Z"


def script(path, body, version="agy version 1.1.15"):
    """Write an agy stand-in that passes the compatibility probe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys, time\n"
        f"if '--version' in sys.argv:\n    print({version!r}); sys.exit()\n"
        f"if '--help' in sys.argv:\n    print({HELP!r}); sys.exit()\n"
        + body,
    )
    path.chmod(0o755)
    return str(path)


def auth_cli(directory, body, version="1.1.27"):
    """Write an agy stand-in whose only other command is /usage."""
    binary = directory / "agy"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(
        "#!/usr/bin/env python3\nimport sys, time, os\n"
        f'if sys.argv[1] == "--version": print("{version}"); '
        "sys.exit()\n"
        f'if sys.argv[1] == "--help": print("{HELP}"); sys.exit()\n'
        'assert sys.argv[1:] == ["--print", "/usage"]\n' + body,
    )
    binary.chmod(0o755)
    return str(binary)


@pytest.fixture
def cli(tmp_path):
    return script(tmp_path / "agy", STREAM)


@pytest.fixture(name="runtime")
async def make_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    state = workspace / "plugin-data" / PLUGIN_ID
    state.mkdir(parents=True)
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

    def backend(binary=None):
        value = AntigravityBackend(context, binary=binary)
        backends.append(value)
        return value

    def call(user="alice", session="one", settings=None, cwd=None):
        return HarnessCallContext(
            f"{user}-{session}",
            session,
            user,
            "console",
            cwd or workspace,
            dict(settings or {}),
            None,
            context.resources.child(instance_id="call"),
        )

    def stored():
        return json.loads((state / SESSIONS).read_text(encoding="utf-8"))

    yield SimpleNamespace(
        context=context,
        workspace=workspace,
        state=state,
        backend=backend,
        call=call,
        stored=stored,
    )
    for value in backends:
        await value.close()


async def turn(backend, call, prompt="hello", attachments=()):
    request = HarnessRunRequest(prompt, attachments)
    async with asyncio.timeout(20):
        return [event async for event in backend.run_turn(call, request)]


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
    assert capabilities.tool_stream
    assert capabilities.session_resume
    assert not capabilities.authentication
    assert not capabilities.attachments
    assert not capabilities.reasoning_stream
    assert not capabilities.qwenpaw_mcp_projection
    assert not capabilities.qwenpaw_skills_projection
    assert not capabilities.mcp_tool_allowlist
    assert not capabilities.external_history
    assert capabilities.commands == ()
    assert [p.settings for p in capabilities.approval_presets] == [
        {"permission_mode": "configured"},
        {"permission_mode": "full-access"},
    ]


async def test_reuses_process_persists_and_resumes(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    first = await turn(backend, call)
    second = await turn(backend, call)
    assert first[-1].kind == second[-1].kind == Kind.COMPLETED
    assert first[0].text == second[0].text
    data = runtime.stored()
    assert data["version"] == 1
    assert data["sessions"][KEY]["conversation_id"] == "conversation-1"
    await backend.close()
    third = await turn(runtime.backend(cli), call)
    assert third[-1].kind == Kind.COMPLETED
    assert third[0].text != first[0].text


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
    assert KEY in runtime.stored()["sessions"]


async def test_eof_is_error(runtime, cli):
    events = await turn(runtime.backend(cli), runtime.call(), "eof")
    assert events[-1].kind == Kind.ERROR
    assert "EOF without result" in events[-1].text


@pytest.mark.parametrize(
    "settings",
    [
        {"print_timeout_seconds": 0},
        {"print_timeout_seconds": True},
        {"idle_timeout_seconds": 29},
        {"permission_mode": "unsafe"},
        {"reasoning_effort": "ultra"},
        {"model": 3},
    ],
)
async def test_rejects_invalid_settings(runtime, cli, settings):
    backend = runtime.backend(cli)
    events = await turn(backend, runtime.call(settings=settings))
    assert [event.kind for event in events] == [Kind.ERROR]
    assert not backend._processes


async def test_rejects_attachments_and_slash_prompts(runtime, cli, tmp_path):
    backend = runtime.backend(cli)
    attachment = HarnessAttachment(HarnessAttachmentKind.FILE, tmp_path)
    events = await turn(backend, runtime.call(), attachments=(attachment,))
    assert "attachments" in events[-1].text
    events = await turn(backend, runtime.call(), " /compact")
    assert "slash commands" in events[-1].text
    assert not backend._processes


@pytest.mark.parametrize(
    "settings, tail",
    [
        ({"reasoning_effort": "max"}, ["--effort", "max"]),
        ({"model": "default", "effort": "low"}, ["--effort", "low"]),
        # A listed model id already names its effort; agy rejects any other.
        (
            {"model": "gemini-3.8-flash-low", "reasoning_effort": "high"},
            ["--model", "gemini-3.8-flash-low"],
        ),
        (
            {"model": "claude-sonnet-4-6", "effort": "low"},
            ["--model", "claude-sonnet-4-6"],
        ),
        (
            {"permission_mode": "full-access"},
            ["stream-json", "--print-timeout", "3600s"],
        ),
    ],
)
def test_effort_only_selects_default_model_variant(settings, tail):
    argv = build_argv("agy", validate_settings(settings), None)
    if settings.get("permission_mode") == "full-access":
        assert argv[-1] == "--dangerously-skip-permissions"
        argv = argv[:-1]
    assert argv[-len(tail) :] == tail
    assert argv.count("--effort") == (tail[0] == "--effort")


def test_resume_argv_names_the_saved_conversation():
    argv = build_argv(
        "agy",
        validate_settings({}),
        {"conversation_id": "saved", "cwd": "/"},
    )
    assert argv[argv.index("--conversation") + 1] == "saved"
    assert "--dangerously-skip-permissions" not in argv


async def test_settings_restart_preserves_mapping(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    first = await turn(backend, call)
    second = await turn(
        backend,
        runtime.call(settings={"effort": "high"}),
    )
    assert first[0].text != second[0].text
    assert backend._sessions[KEY]["conversation_id"] == "conversation-1"


async def test_independent_identities_and_serial_turns(runtime, cli):
    backend, call = runtime.backend(cli), runtime.call()
    first, second = await asyncio.gather(
        turn(backend, call),
        turn(backend, call),
    )
    assert first[0].text == second[0].text
    other_session = await turn(backend, runtime.call(session="two"))
    other_user = await turn(backend, runtime.call(user="bob"))
    pids = {first[0].text, other_session[0].text, other_user[0].text}
    assert len(pids) == 3
    assert set(runtime.stored()["sessions"]) == {
        KEY,
        json.dumps(["alice", "console", "two"]),
        json.dumps(["bob", "console", "one"]),
    }


async def test_cancellation_reaps_process_keeps_init(runtime, tmp_path):
    binary = script(
        tmp_path / "wait-agy",
        "for line in sys.stdin:\n"
        "    print(json.dumps({'event': 'init', "
        "'conversation_id': 'wait-id'}), flush=True)\n"
        "    time.sleep(100)\n",
    )
    backend = runtime.backend(binary)
    task = asyncio.create_task(turn(backend, runtime.call()))
    async with asyncio.timeout(10):
        while KEY not in backend._sessions:
            await asyncio.sleep(0.01)
    process = backend._processes[KEY].process
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None
    assert backend._sessions[KEY]["conversation_id"] == "wait-id"
    assert not backend._processes


async def test_resume_id_mismatch_never_overwrites_mapping(runtime, cli):
    backend = runtime.backend(cli)
    await turn(backend, runtime.call())
    await backend.close()
    path = Path(cli)
    path.write_text(
        path.read_text().replace(
            "'conversation_id': conversation",
            "'conversation_id': 'wrong'",
        ),
    )
    backend = runtime.backend(cli)
    events = await turn(backend, runtime.call())
    assert events[-1].kind == Kind.ERROR
    assert runtime.stored()["sessions"][KEY]["conversation_id"] == (
        "conversation-1"
    )


async def test_result_before_init_is_only_error(runtime, tmp_path):
    binary = script(
        tmp_path / "bad-agy",
        "for line in sys.stdin:\n"
        "    print(json.dumps({'event': 'result', 'result': {"
        "'status': 'SUCCESS', 'response': 'bad'}}), flush=True)\n",
    )
    events = await turn(runtime.backend(binary), runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]


async def test_usage_persisted_and_delta_only_with_known_baseline(
    runtime,
    cli,
):
    path = Path(cli)
    path.write_text(
        path.read_text().replace(
            "'response': str(os.getpid())",
            "'response': str(os.getpid()), 'usage': {'total_tokens': 42}",
        ),
    )
    backend = runtime.backend(cli)
    first = await turn(backend, runtime.call())
    assert first[-1].data["usage_delta"] is None
    assert first[-1].data["usage_delta_scope"] == "unknown"
    await backend.close()
    path.write_text(
        path.read_text().replace("'total_tokens': 42", "'total_tokens': 50"),
    )
    second = await turn(runtime.backend(cli), runtime.call())
    assert second[-1].data["usage_delta"] == {"total_tokens": 8}
    assert runtime.stored()["sessions"][KEY]["last_usage"] == {
        "total_tokens": 50,
    }


async def test_success_stderr_is_visible_and_redacted(runtime, cli):
    path = Path(cli)
    target = "prompt = json.loads(line)['message']['content']"
    path.write_text(
        path.read_text().replace(
            target,
            target + "\n    print('permission denied token=secret-value', "
            "file=sys.stderr, flush=True)",
        ),
    )
    events = await turn(runtime.backend(cli), runtime.call())
    assert any(
        event.kind == Kind.TOOL_COMPLETED and "permission denied" in event.text
        for event in events
    )
    assert "secret-value" not in str(events)


@pytest.mark.parametrize(
    "sequence, outcomes",
    [
        (["turn1", "turn2", None, "resume"], ["completed"] * 3),
        (
            ["tool-allowed", "cancel", None, "cancel-resume"],
            ["completed", "error", "completed"],
        ),
        (["tool-configured"], ["completed"]),
    ],
)
async def test_recorded_agy_turns_replay(
    runtime,
    tmp_path,
    sequence,
    outcomes,
):
    # Each prompt names a recorded NDJSON turn; None closes the transport
    # so the next turn resumes with --conversation in a new process.
    binary = script(
        tmp_path / "replay-agy",
        "for line in sys.stdin:\n"
        "    prompt = json.loads(line)['message']['content']\n"
        f"    path = os.path.join({str(FIXTURES)!r}, prompt + '.ndjson')\n"
        "    sys.stdout.write(open(path).read()); sys.stdout.flush()\n",
    )
    backend, results = runtime.backend(binary), []
    for name in sequence:
        if name is None:
            await backend.close()
            backend = runtime.backend(binary)
            continue
        results.append(
            await turn(backend, runtime.call(), name),
        )
    assert [events[-1].kind.value for events in results] == outcomes
    text = [
        event.text
        for events in results
        for event in events
        if event.kind == Kind.TEXT_DELTA
    ]
    assert all(text) and text
    if sequence[0].startswith("tool"):
        kinds = [event.kind for event in results[0]]
        assert Kind.TOOL_STARTED in kinds and Kind.TOOL_COMPLETED in kinds


async def test_legacy_store_is_copied_once_and_claimed(runtime, cli):
    legacy = runtime.workspace / "harnesses" / SESSIONS
    legacy.parent.mkdir()
    record = {
        "conversation_id": "legacy-id",
        "cwd": str(runtime.workspace.resolve()),
        "settings_fingerprint": "old",
        "last_usage": {"total_tokens": 5},
    }
    original = json.dumps({"version": 1, "sessions": {"one": record}})
    legacy.write_text(original)
    backend = runtime.backend(cli)
    # The stand-in echoes --conversation; anything else is a mismatch.
    events = await turn(backend, runtime.call())
    assert events[-1].kind == Kind.COMPLETED
    sessions = runtime.stored()["sessions"]
    assert list(sessions) == [KEY]
    assert sessions[KEY]["conversation_id"] == "legacy-id"
    await turn(backend, runtime.call(user="bob"))
    bob = runtime.stored()["sessions"][json.dumps(["bob", "console", "one"])]
    assert bob["conversation_id"] == "conversation-1"
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
    assert await backend.history(call) == ()


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


async def test_model_list_cache_and_fallback(runtime, tmp_path):
    binary = tmp_path / "agy"
    binary.write_text('#!/bin/sh\nprintf "model-one\\tModel One\\n"\n')
    binary.chmod(0o755)
    backend, call = runtime.backend(str(binary)), runtime.call()
    models = await backend.models(call)
    assert models[1].id == "model-one"
    binary.write_text("#!/bin/sh\nexit 1\n")
    assert (await backend.models(call))[1].id == "model-one"
    fallback = await runtime.backend(str(binary)).models(call)
    assert [model.id for model in fallback] == ["default"]
    assert fallback[0].is_default
    assert "failed" in fallback[0].description


async def test_model_output_is_read_until_eof(runtime, tmp_path):
    binary = tmp_path / "agy"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('one\\tOne', flush=True)\n"
        "time.sleep(.1)\n"
        "print('two\\tTwo', flush=True)\n",
    )
    binary.chmod(0o755)
    models = await runtime.backend(str(binary)).models(runtime.call())
    assert [model.id for model in models] == ["default", "one", "two"]


async def test_status_checks_version_and_required_flags(runtime, tmp_path):
    binary = tmp_path / "agy"
    binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'agy version 1.1.15'; "
        "exit; fi\n"
        "if [ \"$1\" = \"--help\" ]; then echo 'legacy CLI'; exit; fi\n"
        "exit 97\n",
    )
    binary.chmod(0o755)
    backend = runtime.backend(str(binary))
    status = await backend.status(runtime.call())
    assert status.installed
    assert not status.available
    assert "flags" in status.error
    assert not status.authenticated and status.account is None
    events = await turn(backend, runtime.call())
    assert [event.kind for event in events] == [Kind.ERROR]
    assert "flags" in events[0].text


# Authentication


@pytest.mark.parametrize(
    "body, state",
    [
        (f"print({ROW!r})\n", "authenticated"),
        ('print("no quota")\n', "unknown"),
        (
            'print("Authentication required", file=sys.stderr, flush=True)\n'
            "time.sleep(30)\n",
            "unauthenticated",
        ),
    ],
)
async def test_status_reports_tri_state_through_account(
    runtime,
    tmp_path,
    body,
    state,
):
    backend = runtime.backend(auth_cli(tmp_path, body))
    status = await backend.status(runtime.call())
    assert status.installed and status.available
    assert status.authenticated is (state == "authenticated")
    assert status.account == {
        "cli_version": "1.1.27",
        "authentication_status": state,
    }
    assert bool(status.error) is (state != "authenticated")


async def test_quota_confirms_login_in_isolated_directory(tmp_path):
    binary = auth_cli(
        tmp_path,
        'assert sys.stdin.read() == ""\n'
        f"assert os.getcwd() != {str(tmp_path)!r}\n"
        'assert os.environ["BROWSER"] == "/bin/false"\n'
        f"print({ROW!r})\n",
    )
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authenticated
    assert status.authentication_status == "authenticated"
    assert status.account == {"cli_version": "1.1.27"}


async def test_help_flags_on_stderr(tmp_path):
    binary = auth_cli(tmp_path, f"print({ROW!r})\n")
    path = Path(binary)
    path.write_text(
        path.read_text().replace(
            f'print("{HELP}")',
            f'print("{HELP}", file=sys.stderr)',
        ),
    )
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.available
    assert status.authenticated


async def test_relative_binary_survives_isolated_probe(tmp_path, monkeypatch):
    auth_cli(tmp_path, f"print({ROW!r})\n")
    monkeypatch.chdir(tmp_path)
    status = await discovery.Discovery().status("./agy", dict(os.environ))
    assert status.authenticated
    assert status.runtime_path == str(tmp_path / "agy")


def test_invalid_configured_binary_explains_problem(tmp_path):
    status = discovery.discover(str(tmp_path / "missing"), dict(os.environ))
    assert "configured" in status.error.lower()
    assert "executable" in status.error.lower()


@pytest.mark.parametrize(
    "body, state",
    [
        (
            'print("Authentication required", file=sys.stderr, flush=True)\n'
            "time.sleep(30)\n",
            "unauthenticated",
        ),
        (
            'print("Error: authentication required. Run agy", '
            "file=sys.stderr, flush=True)\ntime.sleep(30)\n",
            "unauthenticated",
        ),
        (
            'print("network token=SECRET", file=sys.stderr)\nsys.exit(1)\n',
            "unknown",
        ),
        ('print("no quota")\n', "unknown"),
        (f'print({ROW.replace("97%", "101%")!r})\n', "unknown"),
        ('print("x" * (1024 * 1024 + 1))\n', "unknown"),
    ],
)
async def test_safe_failure_status(tmp_path, body, state):
    status = await asyncio.wait_for(
        discovery.Discovery().status(
            auth_cli(tmp_path, body),
            dict(os.environ),
        ),
        5,
    )
    assert status.available
    assert not status.authenticated
    assert status.authentication_status == state
    assert status.error
    assert "SECRET" not in repr(status)


@pytest.mark.parametrize("version", ["1.1.24", "1.0.30", "garbage"])
async def test_unsupported_version_never_runs_usage(tmp_path, version):
    marker = tmp_path / "called"
    binary = auth_cli(
        tmp_path,
        f'open({str(marker)!r}, "w").close()\n',
        version,
    )
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authentication_status == "unknown"
    assert not marker.exists()


@pytest.mark.parametrize("version", ["1.1.25", "1.2.11", "agy version 2.0.0"])
async def test_newer_versions_run_usage(tmp_path, version):
    binary = auth_cli(tmp_path, f"print({ROW!r})\n", version)
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authentication_status == "authenticated"


async def test_timeout_is_unknown_and_cleanup_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "PROBE_TIMEOUT", 0.1)
    status = await discovery.Discovery().status(
        auth_cli(tmp_path, "time.sleep(30)\n"),
        dict(os.environ),
    )
    assert status.available
    assert status.authentication_status == "unknown"
    assert "timed out" in status.error


async def test_cancellation_closes_process(tmp_path, monkeypatch):
    started = asyncio.Event()
    captured = []
    original = auth.StreamProcess.start

    async def start(*args):
        process = await original(*args)
        captured.append(process)
        started.set()
        return process

    monkeypatch.setattr(auth.StreamProcess, "start", start)
    task = asyncio.create_task(
        auth.probe_auth(
            auth_cli(tmp_path, "time.sleep(30)\n"),
            dict(os.environ),
            "1.1.27",
        ),
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[0].process.returncode is not None
    assert captured[0].drain_task.done()


async def test_cache_environment_expiry_and_single_probe(
    tmp_path,
    monkeypatch,
):
    calls = []

    async def probe(binary, env, version):
        del binary, version
        calls.append(dict(env))
        await asyncio.sleep(0)
        return auth.AuthResult("authenticated")

    monkeypatch.setattr(discovery, "probe_auth", probe)
    cache = discovery.Discovery()
    binary = auth_cli(tmp_path, "sys.exit(99)\n")
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


async def test_unknown_cache_recovers_quickly(tmp_path):
    cache = discovery.Discovery()
    binary = auth_cli(tmp_path, 'print("no data")\n')
    await cache.status(binary, dict(os.environ))
    assert 0 < cache._expires - discovery.time.monotonic() <= 5


@pytest.mark.parametrize(
    "row",
    [
        "",
        ROW.replace("97%", "-1%"),
        ROW.replace("97%", "NaN%"),
        ROW.replace("2026-09-15", "2026-99-15"),
        ROW.replace("Gemini Models", ""),
        ROW.replace("Z", ""),
    ],
)
def test_malformed_quota(row):
    assert not auth.valid_quota(row)


# Events


def test_done_text_delta_is_emitted_without_result_duplication():
    mapper = EventMapper("chat", "turn")
    events = mapper.map(
        {
            "event": "step_update",
            "step_update": {
                "step_type": "agent_response",
                "state": "DONE",
                "text_delta": "tail",
            },
        },
    )
    assert [event.text for event in events] == ["tail"]
    events = mapper.map(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": "tail",
                "usage": {"total_tokens": 42},
            },
        },
    )
    assert len(events) == 1
    assert events[0].kind == Kind.COMPLETED
    assert events[0].data["usage_scope"] == "provider_cumulative_raw"
    assert events[0].data["provider_result"]["usage"]["total_tokens"] == 42


def test_result_fallback_and_failure():
    mapper = EventMapper("chat", "turn")
    events = mapper.map(
        {
            "event": "result",
            "result": {"status": "INTERRUPTED", "response": "partial"},
        },
    )
    assert [event.kind for event in events] == [
        Kind.TEXT_DELTA,
        Kind.CANCELLED,
    ]


def test_tools_have_turn_scoped_ids_arguments_and_output():
    mapper = EventMapper("chat", "turn")
    raw = {
        "event": "step_update",
        "step_update": {
            "step_index": 2,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "run_command",
            "tool_info": {
                "parameters": {"CommandLine": "printf test"},
                "output": "test",
            },
        },
    }
    events = mapper.map(raw)
    assert [event.kind for event in events] == [
        Kind.TOOL_STARTED,
        Kind.TOOL_COMPLETED,
    ]
    assert events[0].item_id == "chat:turn:2"
    assert events[0].data["arguments"] == {"CommandLine": "printf test"}
    assert events[1].text == "test"


def test_replay_fixture_turn1():
    mapper = EventMapper("chat", "turn1")
    events = []
    for line in (FIXTURES / "turn1.ndjson").read_text("utf-8").splitlines():
        if line.strip():
            events.extend(mapper.map(json.loads(line)))
    kinds = [event.kind.value for event in events]
    assert "text_delta" in kinds
    assert kinds[-1] == "completed"


# Models

MODELS = (
    "Fetching available models...\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
)
EFFORT_ARGS = ["--output-format", "json", "-p", "/effort"]


def effort_reply(data):
    return json.dumps(
        {"status": "SUCCESS", "command": {"name": "effort", "data": data}},
    )


def models_cli(tmp_path, effort_body):
    binary = tmp_path / "agy"
    binary.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        'if sys.argv[1:] == ["models"]:\n'
        f'    print({MODELS!r}, end=""); sys.exit()\n'
        f"assert sys.argv[1:] == {EFFORT_ARGS!r}\n"
        f'open({str(tmp_path / "effort-probed")!r}, "w").close()\n'
        + effort_body,
    )
    binary.chmod(0o755)
    return str(binary)


async def test_listed_variants_have_no_separate_effort(tmp_path):
    reply = effort_reply(
        {
            "adjustable": True,
            "current": "high",
            "available": ["low", "high", "max"],
        },
    )
    binary = models_cli(tmp_path, f"print({reply!r})\n")
    default, *variants = await ModelDiscovery().models(
        binary,
        dict(os.environ),
        "1.2.11",
    )
    assert default.is_default and default.id == "default"
    assert default.reasoning_efforts == ("low", "high", "max")
    assert [model.id for model in variants] == [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-low",
        "claude-sonnet-4-6",
    ]
    assert all(model.reasoning_efforts == () for model in variants)


@pytest.mark.parametrize(
    "effort_body",
    [
        f"print({effort_reply({'adjustable': False})!r})\n",
        "print({!r})\n".format(
            effort_reply({"adjustable": True, "available": ["low", "turbo"]}),
        ),
        'print("not json")\n',
        "sys.exit(1)\n",
    ],
)
async def test_default_efforts_follow_cli_report(tmp_path, effort_body):
    binary = models_cli(tmp_path, effort_body)
    default = (
        await ModelDiscovery().models(
            binary,
            dict(os.environ),
            "agy version 1.1.15",
        )
    )[0]
    expected = ("low",) if "turbo" in effort_body else ()
    assert default.reasoning_efforts == expected


@pytest.mark.parametrize("version", ["1.1.10", "unknown", None])
async def test_old_cli_is_never_asked_for_effort(tmp_path, version):
    # Before 1.1.11, print mode sent "/effort" to the model as a prompt.
    binary = models_cli(tmp_path, 'print("{}")\n')
    default = (
        await ModelDiscovery().models(binary, dict(os.environ), version)
    )[0]
    assert default.reasoning_efforts == ()
    assert not (tmp_path / "effort-probed").exists()


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
    script(bin_dir / "agy", STREAM)
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
