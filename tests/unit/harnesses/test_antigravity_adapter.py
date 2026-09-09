# -*- coding: utf-8 -*-
"""Native stream process and durable session behavior."""
import asyncio
import json

import pytest

from qwenpaw.harnesses.antigravity.adapter import AntigravityAdapter
from qwenpaw.harnesses.antigravity.adapter import build_argv
from qwenpaw.harnesses.antigravity.adapter import validate_settings
from qwenpaw.harnesses.antigravity.session_store import SessionStore


@pytest.fixture
def cli(tmp_path):
    binary = tmp_path / "agy"
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "if '--version' in sys.argv:\n"
        "    print('agy version 1.1.15'); sys.exit()\n"
        "if '--help' in sys.argv:\n"
        "    print('--input-format --output-format --conversation "
        "--print-timeout --model --effort --dangerously-skip-permissions')\n"
        "    sys.exit()\n"
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
    binary.write_text(script)
    binary.chmod(0o755)
    return str(binary)


async def turn(adapter, path, prompt="hello", settings=None):
    return [event async for event in adapter.run_turn(
        session_id="one", prompt=prompt, cwd=path, settings=settings or {},
    )]


async def test_reuses_process_persists_and_resumes(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        first = await turn(adapter, tmp_path)
        second = await turn(adapter, tmp_path)
        assert first[0].text == second[0].text
        data = json.loads((tmp_path / "antigravity_sessions.json").read_text())
        assert data['version'] == 1
        assert data['sessions']['one']['conversation_id'] == 'conversation-1'
        await adapter.stop()
        third = await turn(adapter, tmp_path)
        assert third[0].text != first[0].text
    finally:
        await adapter.stop()


async def test_cwd_requires_explicit_reset(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        await turn(adapter, tmp_path)
        other = tmp_path / 'other'
        other.mkdir()
        events = await turn(adapter, other)
        assert events[-1].kind == 'error'
        assert 'clear' in events[-1].text.lower()
        await adapter.reset_session('one')
        assert (await turn(adapter, other))[-1].kind == 'completed'
    finally:
        await adapter.stop()


async def test_eof_is_error(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        assert (await turn(adapter, tmp_path, 'eof'))[-1].kind == 'error'
    finally:
        await adapter.stop()


@pytest.mark.parametrize('settings', [
    {'print_timeout_seconds': 0}, {'print_timeout_seconds': True},
    {'idle_timeout_seconds': 29}, {'permission_mode': 'unsafe'},
    {'reasoning_effort': 'ultra'},
])
async def test_rejects_invalid_settings(tmp_path, cli, settings):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    events = await turn(adapter, tmp_path, settings=settings)
    assert events[-1].kind == 'error'
    await adapter.stop()


@pytest.mark.parametrize('settings, tail', [
    ({'reasoning_effort': 'max'}, ['--effort', 'max']),
    ({'model': 'default', 'effort': 'low'}, ['--effort', 'low']),
    # A listed model id already names its effort; agy rejects any other.
    ({'model': 'gemini-3.8-flash-low', 'reasoning_effort': 'high'},
     ['--model', 'gemini-3.8-flash-low']),
    ({'model': 'claude-sonnet-4-6', 'effort': 'low'},
     ['--model', 'claude-sonnet-4-6']),
])
def test_effort_only_selects_default_model_variant(settings, tail):
    argv = build_argv('agy', validate_settings(settings), None)
    assert argv[-2:] == tail
    assert argv.count('--effort') == (tail[0] == '--effort')


async def test_settings_restart_preserves_mapping(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        first = await turn(adapter, tmp_path)
        second = await turn(adapter, tmp_path, settings={'effort': 'high'})
        assert first[0].text != second[0].text
        cid = adapter._store.sessions['one']['conversation_id']
        assert cid == 'conversation-1'
    finally:
        await adapter.stop()


async def test_independent_sessions_and_serial_turns(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        first, second = await asyncio.gather(
            turn(adapter, tmp_path), turn(adapter, tmp_path),
        )
        assert first[0].text == second[0].text
        other = [e async for e in adapter.run_turn(
            session_id='two', prompt='hi', cwd=tmp_path, settings={},
        )]
        assert other[0].text != first[0].text
    finally:
        await adapter.stop()


async def test_cancellation_reaps_process_keeps_init(tmp_path):
    binary = tmp_path / 'wait-agy'
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "if '--version' in sys.argv:\n"
        "    print('agy version 1.1.15'); sys.exit()\n"
        "if '--help' in sys.argv:\n"
        "    print('--input-format --output-format --conversation "
        "--print-timeout --model --effort --dangerously-skip-permissions')\n"
        "    sys.exit()\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'event': 'init', "
        "'conversation_id': 'wait-id'}), flush=True)\n"
        "    time.sleep(100)\n"
    )
    binary.write_text(script)
    binary.chmod(0o755)
    adapter = AntigravityAdapter(tmp_path, binary=str(binary))
    task = asyncio.create_task(turn(adapter, tmp_path))
    for _ in range(100):
        if adapter._store.sessions:
            break
        await asyncio.sleep(.01)
    process = adapter._processes['one'].process
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None
    assert adapter._store.sessions['one']['conversation_id'] == 'wait-id'
    assert not adapter._processes


async def test_resume_id_mismatch_never_overwrites_mapping(tmp_path, cli):
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    try:
        await turn(adapter, tmp_path)
        await adapter.stop()
        from pathlib import Path
        path = Path(cli)
        path.write_text(path.read_text().replace(
            "'conversation_id': conversation",
            "'conversation_id': 'wrong'",
        ))
        events = await turn(adapter, tmp_path)
        assert events[-1].kind == 'error'
        cid = adapter._store.sessions['one']['conversation_id']
        assert cid == 'conversation-1'
    finally:
        await adapter.stop()


async def test_result_before_init_is_only_error(tmp_path):
    binary = tmp_path / 'bad-agy'
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('agy version 1.1.15'); sys.exit()\n"
        "if '--help' in sys.argv:\n"
        "    print('--input-format --output-format --conversation "
        "--print-timeout --model --effort --dangerously-skip-permissions')\n"
        "    sys.exit()\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'event': 'result', 'result': {"
        "'status': 'SUCCESS', 'response': 'bad'}}), flush=True)\n"
    )
    binary.write_text(script)
    binary.chmod(0o755)
    adapter = AntigravityAdapter(tmp_path, binary=str(binary))
    events = await turn(adapter, tmp_path)
    assert [e.kind for e in events] == ['error']
    await adapter.stop()


async def test_usage_persisted_and_delta_only_with_known_baseline(
    tmp_path, cli,
):
    from pathlib import Path
    path = Path(cli)
    path.write_text(path.read_text().replace(
        "'response': str(os.getpid())",
        "'response': str(os.getpid()), 'usage': {'total_tokens': 42}",
    ))
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    first = await turn(adapter, tmp_path)
    assert first[-1].data['usage_delta'] is None
    await adapter.stop()
    path.write_text(path.read_text().replace(
        "'total_tokens': 42", "'total_tokens': 50",
    ))
    second = await turn(adapter, tmp_path)
    assert second[-1].data['usage_delta'] == {'total_tokens': 8}
    assert adapter._store.sessions['one']['last_usage'] == {
        'total_tokens': 50,
    }
    await adapter.stop()


async def test_success_stderr_is_visible_and_redacted(tmp_path, cli):
    from pathlib import Path
    path = Path(cli)
    target = "prompt = json.loads(line)['message']['content']"
    replacement = (
        "prompt = json.loads(line)['message']['content']\n"
        "    print('permission denied token=secret-value', "
        "file=sys.stderr, flush=True)"
    )
    path.write_text(path.read_text().replace(target, replacement))
    adapter = AntigravityAdapter(tmp_path, binary=cli)
    events = await turn(adapter, tmp_path)
    assert any(
        e.kind == 'tool_completed' and 'permission denied' in e.text
        for e in events
    )
    assert 'secret-value' not in str(events)
    await adapter.stop()


def test_unreadable_session_store_starts_empty(tmp_path):
    path = tmp_path / "antigravity_sessions.json"
    path.write_text("{not json", encoding="utf-8")
    store = SessionStore(tmp_path)
    assert store.sessions == {}

    # Saving the empty store must not destroy the unreadable original.
    store.save()
    backup = tmp_path / "antigravity_sessions.json.unreadable"
    assert backup.read_text(encoding="utf-8") == "{not json"
