# -*- coding: utf-8 -*-
"""CLI compatibility and bounded authentication discovery."""
import asyncio
import os
import shutil
import sys
import time

from ..events import HarnessProvider
from .process import StreamProcess
from .auth import probe_auth


def discover(binary=None, env=None):
    environment = os.environ if env is None else env
    search_path = environment.get('PATH', os.defpath)
    home = environment.get('HOME') or os.path.expanduser('~')
    source = 'configured' if binary else 'PATH'
    if binary:
        configured = os.fspath(binary)
        if configured.startswith('~/'):
            configured = os.path.join(home, configured[2:])
        path = shutil.which(configured, path=search_path)
    else:
        path = shutil.which('agy', path=search_path)
        if not path:
            path = shutil.which(os.path.join(home, '.local', 'bin', 'agy'))
            if path:
                source = 'user-local'
    if path:
        path = os.path.abspath(path)
    supported = sys.platform == 'linux'
    error = None
    if not supported:
        error = 'Antigravity Harness is supported on Linux only.'
    elif not path:
        if binary:
            error = (
                'Configured Antigravity CLI binary was not found or is not '
                'executable. Check the configured path and service PATH.'
            )
        else:
            error = (
                'Install Antigravity CLI (agy) and add it to PATH, '
                'or configure binary.'
            )
    return HarnessProvider(
        id='antigravity',
        name='Antigravity',
        available=bool(path and supported),
        installed=bool(path),
        authenticated=False,
        authentication_status='unknown',
        runtime_path=path,
        runtime_source=source,
        error=error,
    )


class Discovery:
    """Cache compatibility and authentication per executable/environment."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._expires = 0
        self._cached = None
        self._key = None

    async def status(self, binary, env):
        async with self._lock:
            status = discover(binary, env)
            key = (
                binary, status.runtime_path, status.runtime_source,
                status.available,
                os.path.realpath(status.runtime_path)
                if status.runtime_path else None,
                tuple(sorted(env.items())),
            )
            if (self._cached and self._key == key
                    and time.monotonic() < self._expires):
                return self._cached.model_copy(deep=True)
            if status.available:
                try:
                    output = {}
                    for flag in ('--version', '--help'):
                        process = await StreamProcess.start(
                            [status.runtime_path, flag], '.', env,
                        )
                        try:
                            process.process.stdin.close()
                            data = bytearray()
                            async with asyncio.timeout(5):
                                while chunk := await (
                                    process.process.stdout.read(8192)
                                ):
                                    data.extend(chunk)
                                    if len(data) > 1024 * 1024:
                                        raise RuntimeError(
                                            'agy probe output too large',
                                        )
                                await process.process.wait()
                                await process.drain_task
                            if process.process.returncode:
                                raise RuntimeError(
                                    'agy compatibility probe failed',
                                )
                            output[flag] = data.decode(errors='replace')
                            if flag == '--help':
                                output[flag] += process.stderr.decode(
                                    errors='replace',
                                )
                        finally:
                            await asyncio.shield(process.close())
                    required = (
                        '--input-format', '--output-format',
                        '--conversation', '--print-timeout',
                        '--model', '--effort',
                        '--dangerously-skip-permissions',
                    )
                    if not all(flag in output['--help'] for flag in required):
                        raise RuntimeError(
                            'agy is missing required streaming flags; '
                            'upgrade Antigravity CLI',
                        )
                    if not output['--version'].strip():
                        raise RuntimeError('agy did not report its version')
                    status.account = {
                        'cli_version': output['--version'].strip(),
                    }
                except Exception as exc:
                    status.available = False
                    status.error = (
                        str(exc) or 'agy compatibility probe timed out'
                    )
                if status.available:
                    auth = await probe_auth(
                        status.runtime_path, env,
                        status.account['cli_version'],
                    )
                    status.authentication_status = auth.state
                    status.authenticated = auth.state == 'authenticated'
                    status.error = auth.reason
            self._cached = status
            self._key = key
            ttl = 60 if status.authenticated else 5
            self._expires = time.monotonic() + ttl
            return status.model_copy(deep=True)
