# -*- coding: utf-8 -*-
import asyncio
import os
from pathlib import Path

import pytest

from qwenpaw.harnesses.antigravity import discovery
from qwenpaw.harnesses.antigravity import auth


HELP = ('--input-format --output-format --conversation --print-timeout '
        '--model --effort --dangerously-skip-permissions')
ROW = 'Gemini Models\tWeekly Limit Remaining\t97%\t2026-09-15T22:05:50Z'


def cli(tmp_path, body, version='1.1.27'):
    binary = tmp_path / 'agy'
    binary.write_text(
        '#!/usr/bin/env python3\nimport sys, time, os\n'
        f'if sys.argv[1] == "--version": print("{version}"); '
        'sys.exit()\n'
        f'if sys.argv[1] == "--help": print("{HELP}"); sys.exit()\n'
        'assert sys.argv[1:] == ["--print", "/usage"]\n' + body,
    )
    binary.chmod(0o755)
    return str(binary)


async def test_quota_confirms_login_in_isolated_directory(tmp_path):
    binary = cli(tmp_path, 'assert sys.stdin.read() == ""\n'
                 f'assert os.getcwd() != {str(tmp_path)!r}\n'
                 'assert os.environ["BROWSER"] == "/bin/false"\n'
                 f'print({ROW!r})\n')
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authenticated
    assert status.authentication_status == 'authenticated'
    assert status.account == {'cli_version': '1.1.27'}


async def test_help_flags_on_stderr(tmp_path):
    binary = cli(tmp_path, f'print({ROW!r})\n')
    path = Path(binary)
    path.write_text(path.read_text().replace(
        f'print("{HELP}")', f'print("{HELP}", file=sys.stderr)',
    ))
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.available
    assert status.authenticated


async def test_relative_binary_survives_isolated_probe(tmp_path, monkeypatch):
    cli(tmp_path, f'print({ROW!r})\n')
    monkeypatch.chdir(tmp_path)
    status = await discovery.Discovery().status('./agy', dict(os.environ))
    assert status.authenticated
    assert status.runtime_path == str(tmp_path / 'agy')


def test_invalid_configured_binary_explains_problem(tmp_path):
    status = discovery.discover(str(tmp_path / 'missing'), dict(os.environ))
    assert 'configured' in status.error.lower()
    assert 'executable' in status.error.lower()


@pytest.mark.parametrize('body,state', [
    ('print("Authentication required", file=sys.stderr, flush=True)\n'
     'time.sleep(30)\n', 'unauthenticated'),
    ('print("Error: authentication required. Run agy", '
     'file=sys.stderr, flush=True)\ntime.sleep(30)\n', 'unauthenticated'),
    ('print("network token=SECRET", file=sys.stderr)\nsys.exit(1)\n',
     'unknown'),
    ('print("no quota")\n', 'unknown'),
    (f'print({ROW.replace("97%", "101%")!r})\n', 'unknown'),
    ('print("x" * (1024 * 1024 + 1))\n', 'unknown'),
])
async def test_safe_failure_status(tmp_path, body, state):
    status = await asyncio.wait_for(
        discovery.Discovery().status(cli(tmp_path, body), dict(os.environ)),
        5,
    )
    assert status.available
    assert not status.authenticated
    assert status.authentication_status == state
    assert status.error
    assert 'SECRET' not in status.model_dump_json()


@pytest.mark.parametrize('version', ['1.1.24', '1.0.30', 'garbage'])
async def test_unsupported_version_never_runs_usage(tmp_path, version):
    marker = tmp_path / 'called'
    binary = cli(tmp_path, f'open({str(marker)!r}, "w").close()\n', version)
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authentication_status == 'unknown'
    assert not marker.exists()


@pytest.mark.parametrize('version', ['1.1.25', '1.2.11', 'agy version 2.0.0'])
async def test_newer_versions_run_usage(tmp_path, version):
    binary = cli(tmp_path, f'print({ROW!r})\n', version)
    status = await discovery.Discovery().status(binary, dict(os.environ))
    assert status.authentication_status == 'authenticated'


async def test_timeout_is_unknown_and_cleanup_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'PROBE_TIMEOUT', 0.1)
    status = await discovery.Discovery().status(
        cli(tmp_path, 'time.sleep(30)\n'), dict(os.environ),
    )
    assert status.available
    assert status.authentication_status == 'unknown'
    assert 'timed out' in status.error


async def test_cancellation_closes_process(tmp_path, monkeypatch):
    started = asyncio.Event()
    captured = []
    original = auth.StreamProcess.start

    async def start(*args):
        process = await original(*args)
        captured.append(process)
        started.set()
        return process

    monkeypatch.setattr(auth.StreamProcess, 'start', start)
    task = asyncio.create_task(auth.probe_auth(
        cli(tmp_path, 'time.sleep(30)\n'), dict(os.environ), '1.1.27',
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[0].process.returncode is not None
    assert captured[0].drain_task.done()


async def test_cache_environment_expiry_and_single_probe(
    tmp_path, monkeypatch,
):
    calls = []

    async def probe(binary, env, version):
        calls.append(dict(env))
        await asyncio.sleep(0)
        return auth.AuthResult('authenticated')

    monkeypatch.setattr(discovery, 'probe_auth', probe)
    cache = discovery.Discovery()
    binary = cli(tmp_path, 'sys.exit(99)\n')
    env = dict(os.environ)
    results = await asyncio.gather(*(cache.status(binary, env)
                                     for _ in range(3)))
    assert len(calls) == 1
    results[0].account.clear()
    assert (await cache.status(binary, env)).account
    await cache.status(binary, {**env, 'HOME': '/different'})
    assert len(calls) == 2
    cache._expires = 0
    await cache.status(binary, env)
    assert len(calls) == 3


async def test_unknown_cache_recovers_quickly(tmp_path, monkeypatch):
    cache = discovery.Discovery()
    binary = cli(tmp_path, 'print("no data")\n')
    await cache.status(binary, dict(os.environ))
    assert 0 < cache._expires - discovery.time.monotonic() <= 5


@pytest.mark.parametrize('row', [
    '', ROW.replace('97%', '-1%'), ROW.replace('97%', 'NaN%'),
    ROW.replace('2026-09-15', '2026-99-15'),
    ROW.replace('Gemini Models', ''), ROW.replace('Z', ''),
])
def test_malformed_quota(row):
    assert not auth.valid_quota(row)
