# -*- coding: utf-8 -*-
"""CLI resolution plus a bounded ACP compatibility and auth check."""

import asyncio
import copy
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

from .auth import probe_auth
from .io_helpers import run_io


@dataclass
class CliStatus:
    """Installation, compatibility and tri-state authentication of agy."""

    available: bool = False
    installed: bool = False
    authenticated: bool = False
    authentication_status: str = "unknown"
    runtime_path: str | None = None
    runtime_source: str | None = None
    error: str | None = None
    account: dict[str, Any] | None = None


def discover(binary=None, env=None):
    environment = os.environ if env is None else env
    search_path = environment.get("PATH", os.defpath)
    home = environment.get("HOME") or os.path.expanduser("~")
    source = "configured" if binary else "PATH"
    if binary:
        configured = os.fspath(binary)
        if configured.startswith("~/"):
            configured = os.path.join(home, configured[2:])
        path = shutil.which(configured, path=search_path)
    else:
        path = shutil.which("agy", path=search_path)
        if not path:
            path = shutil.which(os.path.join(home, ".local", "bin", "agy"))
            if path:
                source = "user-local"
    if path:
        path = os.path.abspath(path)
    supported = sys.platform == "linux"
    error = None
    if not supported:
        error = "Antigravity Harness is supported on Linux only."
    elif not path:
        if binary:
            error = (
                "Configured Antigravity CLI binary was not found or is not "
                "executable. Check the configured path and service PATH."
            )
        else:
            error = (
                "Install Antigravity CLI (agy) and add it to PATH, "
                "or configure binary."
            )
    return CliStatus(
        available=bool(path and supported),
        installed=bool(path),
        runtime_path=path,
        runtime_source=source,
        error=error,
    )


async def cli_version(binary, env):
    """``agy --version`` with a bounded wait; the group never outlives it."""
    process = await asyncio.create_subprocess_exec(
        binary,
        "--version",
        cwd=".",
        env=env,
        start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            output = await process.stdout.read(64 * 1024)
            await process.wait()
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
    if process.returncode:
        raise RuntimeError("agy --version failed")
    version = output.decode(errors="replace").strip()
    if not version:
        raise RuntimeError("agy did not report its version")
    return version


class Discovery:
    """Cache compatibility and authentication per executable/environment."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._expires = 0
        self._cached = None
        self._key = None

    async def status(self, binary, env):
        async with self._lock:
            status = await run_io(discover, binary, env)
            key = (
                binary,
                status.runtime_path,
                status.runtime_source,
                status.available,
                os.path.realpath(status.runtime_path)
                if status.runtime_path
                else None,
                tuple(sorted(env.items())),
            )
            if (
                self._cached
                and self._key == key
                and time.monotonic() < self._expires
            ):
                return copy.deepcopy(self._cached)
            if status.available:
                try:
                    status.account = {
                        "cli_version": await cli_version(
                            status.runtime_path,
                            env,
                        ),
                    }
                except Exception as exc:
                    status.available = False
                    status.error = str(exc) or "agy --version timed out"
            if status.available:
                auth = await probe_auth(status.runtime_path, env)
                status.available = auth.compatible
                status.authentication_status = auth.state
                status.authenticated = auth.state == "authenticated"
                status.error = auth.reason
            self._cached = status
            self._key = key
            ttl = 60 if status.authenticated else 5
            self._expires = time.monotonic() + ttl
            return copy.deepcopy(status)
