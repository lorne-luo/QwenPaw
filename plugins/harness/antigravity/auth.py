# -*- coding: utf-8 -*-
"""Bounded, non-model authentication checks using the CLI quota report."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from tempfile import TemporaryDirectory
from typing import Literal

from .process import StreamProcess

PROBE_TIMEOUT = 15
OUTPUT_LIMIT = 1024 * 1024
AUTH_MARKER = b"authentication required"


@dataclass(frozen=True)
class AuthResult:
    state: Literal["authenticated", "unauthenticated", "unknown"]
    reason: str | None = None


def version_at_least(version, minimum):
    match = re.fullmatch(
        r"(?:agy version )?(\d+)\.(\d+)\.(\d+)",
        (version or "").strip(),
    )
    return bool(match) and tuple(map(int, match.groups())) >= minimum


def supports_usage(version):
    return version_at_least(version, (1, 1, 25))


def valid_quota(output):
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            continue
        bucket, label, percent, reset = fields
        if not bucket.strip() or not re.fullmatch(
            r"[A-Za-z ]+ Limit Remaining",
            label,
        ):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?%", percent):
            continue
        if not 0 <= float(percent[:-1]) <= 100:
            continue
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            reset,
        ):
            continue
        try:
            datetime.fromisoformat(reset.replace("Z", "+00:00"))
        except ValueError:
            continue
        return True
    return False


async def probe_auth(binary, env, version):
    if not supports_usage(version):
        return AuthResult(
            "unknown",
            "CLI version has no verified auth probe.",
        )
    process = None
    reader = None
    try:
        with TemporaryDirectory(prefix="qwenpaw-agy-auth-") as cwd:
            try:
                process = await StreamProcess.start(
                    [binary, "--print", "/usage"],
                    cwd,
                    {**env, "BROWSER": "/bin/false"},
                )
                process.process.stdin.close()

                async def read_output():
                    data = bytearray()
                    while chunk := await process.process.stdout.read(8192):
                        data.extend(chunk)
                        if len(data) > OUTPUT_LIMIT:
                            raise ValueError("output limit")
                    await process.process.wait()
                    await process.drain_task
                    return data.decode(errors="replace")

                reader = asyncio.create_task(read_output())
                async with asyncio.timeout(PROBE_TIMEOUT):
                    while not reader.done():
                        if AUTH_MARKER in process.stderr.lower():
                            return AuthResult(
                                "unauthenticated",
                                "Antigravity CLI requires login.",
                            )
                        await asyncio.wait({reader}, timeout=0.025)
                    output = await reader
                if AUTH_MARKER in process.stderr.lower():
                    return AuthResult(
                        "unauthenticated",
                        "Antigravity CLI requires login.",
                    )
                if process.process.returncode == 0 and valid_quota(output):
                    return AuthResult("authenticated")
                return AuthResult(
                    "unknown",
                    "Antigravity authentication could not be verified.",
                )
            finally:
                if reader is not None:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                if process is not None:
                    await asyncio.shield(process.close())
    except TimeoutError:
        return AuthResult(
            "unknown",
            "Antigravity authentication probe timed out.",
        )
    except Exception:
        return AuthResult(
            "unknown",
            "Antigravity authentication could not be verified.",
        )
