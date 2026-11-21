# -*- coding: utf-8 -*-
"""CLI compatibility and bounded authentication discovery."""

import asyncio
import copy
import os
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any

from .auth import probe_auth
from .io_helpers import run_io
from .process import StreamProcess

REQUIRED_FLAGS = (
    "--input-format",
    "--output-format",
    "--conversation",
    "--print-timeout",
    "--model",
    "--effort",
    "--dangerously-skip-permissions",
)


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


async def _probe(status, flag, env):
    process = await StreamProcess.start([status.runtime_path, flag], ".", env)
    try:
        process.process.stdin.close()
        data = bytearray()
        async with asyncio.timeout(5):
            while chunk := await process.process.stdout.read(8192):
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise RuntimeError("agy probe output too large")
            await process.process.wait()
            await process.drain_task
        if process.process.returncode:
            raise RuntimeError("agy compatibility probe failed")
        output = data.decode(errors="replace")
        if flag == "--help":
            # agy 1.1.x writes its help text to stderr.
            output += process.stderr.decode(errors="replace")
        return output
    finally:
        await asyncio.shield(process.close())


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
                    output = {
                        flag: await _probe(status, flag, env)
                        for flag in ("--version", "--help")
                    }
                    if not all(
                        flag in output["--help"] for flag in REQUIRED_FLAGS
                    ):
                        raise RuntimeError(
                            "agy is missing required streaming flags; "
                            "upgrade Antigravity CLI",
                        )
                    if not output["--version"].strip():
                        raise RuntimeError("agy did not report its version")
                    status.account = {
                        "cli_version": output["--version"].strip(),
                    }
                except Exception as exc:
                    status.available = False
                    status.error = (
                        str(exc) or "agy compatibility probe timed out"
                    )
                if status.available:
                    auth = await probe_auth(
                        status.runtime_path,
                        env,
                        status.account["cli_version"],
                    )
                    status.authentication_status = auth.state
                    status.authenticated = auth.state == "authenticated"
                    status.error = auth.reason
            self._cached = status
            self._key = key
            ttl = 60 if status.authenticated else 5
            self._expires = time.monotonic() + ttl
            return copy.deepcopy(status)
