# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from qwenpaw.harnesses.antigravity.adapter import AntigravityAdapter
from qwenpaw.harnesses.antigravity.discovery import discover


@pytest.fixture
def discovery_env(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.platform', 'linux')
    home = tmp_path / 'home'
    home.mkdir()
    path = tmp_path / 'bin'
    path.mkdir()
    return {'HOME': str(home), 'PATH': str(path)}


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return path


def test_user_local_fallback(discovery_env):
    binary = executable(Path(discovery_env['HOME']) / '.local/bin/agy')
    status = discover(env=discovery_env)
    assert status.installed and status.available
    assert status.runtime_path == str(binary)
    assert status.runtime_source == 'user-local'


def test_path_precedes_user_local(discovery_env):
    executable(Path(discovery_env['HOME']) / '.local/bin/agy')
    binary = executable(Path(discovery_env['PATH']) / 'agy')
    status = discover(env=discovery_env)
    assert status.runtime_path == str(binary)
    assert status.runtime_source == 'PATH'


@pytest.mark.parametrize('configured', [None, 'agy'])
def test_missing_path_uses_default_not_shell(
    discovery_env, monkeypatch, configured,
):
    executable(Path(discovery_env['PATH']) / 'agy')
    monkeypatch.setenv('PATH', discovery_env['PATH'])
    default_dir = Path(discovery_env['HOME']) / 'default-bin'
    binary = executable(default_dir / 'agy')
    monkeypatch.setattr('os.defpath', str(default_dir))
    status = discover(configured, env={'HOME': discovery_env['HOME']})
    assert status.runtime_path == str(binary)


def test_configured_tilde_uses_supplied_home(discovery_env, monkeypatch):
    monkeypatch.setenv('HOME', '/unrelated-home')
    executable(Path(discovery_env['PATH']) / 'agy')
    executable(Path(discovery_env['HOME']) / '.local/bin/agy')
    binary = executable(Path(discovery_env['HOME']) / 'custom/agy')
    status = discover('~/custom/agy', env=discovery_env)
    assert status.runtime_path == str(binary)
    assert status.runtime_source == 'configured'


def test_configured_symlink_precedes_path(discovery_env):
    home = Path(discovery_env['HOME'])
    executable(Path(discovery_env['PATH']) / 'agy')
    executable(home / '.local/bin/agy')
    binary = home / 'configured'
    binary.symlink_to(executable(home / 'target'))
    status = discover(str(binary), env=discovery_env)
    assert status.available
    assert status.runtime_path == str(binary)
    assert status.runtime_source == 'configured'


@pytest.mark.parametrize('kind', ['missing', 'nonexec', 'directory', 'broken'])
def test_invalid_configured_binary_has_no_fallback(discovery_env, kind):
    home = Path(discovery_env['HOME'])
    executable(Path(discovery_env['PATH']) / 'agy')
    executable(home / '.local/bin/agy')
    binary = home / 'configured'
    if kind == 'nonexec':
        binary.write_text('not executable')
    elif kind == 'directory':
        binary.mkdir()
    elif kind == 'broken':
        binary.symlink_to(home / 'missing-target')
    status = discover(str(binary), env=discovery_env)
    assert not status.installed
    assert not status.available
    assert status.runtime_path is None
    assert status.runtime_source == 'configured'


@pytest.mark.parametrize('kind', ['nonexec', 'directory', 'broken', 'symlink'])
def test_user_local_executable_validation(discovery_env, kind):
    home = Path(discovery_env['HOME'])
    binary = home / '.local/bin/agy'
    binary.parent.mkdir(parents=True)
    if kind == 'nonexec':
        binary.write_text('not executable')
    elif kind == 'directory':
        binary.mkdir()
    else:
        target = home / 'target'
        if kind == 'symlink':
            executable(target)
        binary.symlink_to(target)
    status = discover(env=discovery_env)
    assert status.installed is (kind == 'symlink')
    assert status.available is (kind == 'symlink')
    if kind == 'symlink':
        assert status.runtime_path == str(binary)
        assert status.runtime_source == 'user-local'


def test_missing_and_non_linux(monkeypatch, discovery_env):
    missing = str(Path(discovery_env['HOME']) / 'missing')
    assert not discover(missing, env=discovery_env).installed
    assert 'PATH' in discover(missing, env=discovery_env).error
    executable(Path(discovery_env['HOME']) / '.local/bin/agy')
    monkeypatch.setattr('sys.platform', 'darwin')
    status = discover(env=discovery_env)
    assert status.installed
    assert not status.available
    assert 'Linux' in status.error


async def test_model_list_cache_and_fallback(tmp_path):
    binary = tmp_path / 'agy'
    binary.write_text('#!/bin/sh\nprintf "model-one\\tModel One\\n"\n')
    binary.chmod(0o755)
    adapter = AntigravityAdapter(tmp_path, binary=str(binary))
    models = await adapter.models()
    assert models[1].id == 'model-one'
    binary.write_text('#!/bin/sh\nexit 1\n')
    assert (await adapter.models())[1].id == 'model-one'
    other = AntigravityAdapter(tmp_path, binary=str(binary))
    assert (await other.models())[0].id == ''
    assert 'failed' in other.models_fallback_message
    assert other.capability_unavailable_message is None
    await adapter.stop()
    await other.stop()


async def test_model_output_is_read_until_eof(tmp_path):
    binary = tmp_path / 'agy'
    binary.write_text('''#!/usr/bin/env python3
import time
print('one\\tOne', flush=True)
time.sleep(.1)
print('two\\tTwo', flush=True)
''')
    binary.chmod(0o755)
    adapter = AntigravityAdapter(tmp_path, binary=str(binary))
    assert [m.id for m in await adapter.models()] == ['', 'one', 'two']


async def test_status_checks_version_and_required_flags(tmp_path):
    binary = tmp_path / 'agy'
    binary.write_text('''#!/bin/sh
if [ "$1" = "--version" ]; then echo 'agy version 1.1.15'; exit; fi
if [ "$1" = "--help" ]; then echo 'legacy CLI'; exit; fi
exit 97
''')
    binary.chmod(0o755)
    adapter = AntigravityAdapter(tmp_path, binary=str(binary))
    status = await adapter.status()
    assert status.installed
    assert not status.available
    assert 'flags' in status.error
    assert status.authentication_status == 'unknown'
