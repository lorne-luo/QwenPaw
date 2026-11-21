# -*- coding: utf-8 -*-
"""Bounded read-only model discovery with a short cache."""

import asyncio
import json
import time

from qwenpaw.plugins.harnesses import HarnessModel

from .auth import version_at_least
from .process import StreamProcess

EFFORTS = ("low", "medium", "high", "max")
# A nonempty id keeps generic model pickers from treating the CLI default as
# "nothing selected"; the adapter never passes it to agy as --model.
DEFAULT_MODEL_ID = "default"


class ModelDiscovery:
    def __init__(self):
        self.reason = None
        self._cached = ()
        self._expires = 0
        self._lock = asyncio.Lock()

    async def models(self, binary, env, version=None):
        async with self._lock:
            if time.monotonic() < self._expires:
                return self._cached
            models = []
            try:
                output = await read_output(binary, ["models"], env)
                for line in output.splitlines():
                    columns = line.strip().split("\t", 1)
                    if len(columns) == 2 and columns[0]:
                        # Each listed id already names one effort variant
                        # (e.g. gemini-3.8-flash-low); agy rejects --effort
                        # values that disagree with it.
                        models.append(
                            HarnessModel(id=columns[0], name=columns[1]),
                        )
                if not models:
                    raise RuntimeError(
                        "agy models returned no recognized models",
                    )
                self.reason = None
            except Exception as exc:
                self.reason = str(exc)
            efforts = []
            # Before 1.1.11, print mode sent "/effort" to the model.
            if version_at_least(version, (1, 1, 11)):
                efforts = await default_efforts(binary, env)
            self._cached = (default_model(efforts, self.reason), *models)
            self._expires = time.monotonic() + 60
            return self._cached


async def read_output(binary, args, env):
    """Run one read-only agy command with bounded time and output."""
    if not binary:
        raise RuntimeError("Antigravity CLI is unavailable")
    process = await StreamProcess.start([binary, *args], ".", env)
    try:
        process.process.stdin.close()
        async with asyncio.timeout(10):
            output = bytearray()
            while chunk := await process.process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 1024 * 1024:
                    raise RuntimeError("Antigravity output is too large")
            await process.process.wait()
    finally:
        await asyncio.shield(process.close())
    if process.process.returncode:
        raise RuntimeError(f'agy {" ".join(args)} failed; run agy manually')
    return output.decode()


async def default_efforts(binary, env):
    """Efforts agy accepts for its configured default model, else none."""
    try:
        output = await read_output(
            binary,
            ["--output-format", "json", "-p", "/effort"],
            env,
        )
        data = json.loads(output)["command"]["data"]
        if data["adjustable"] is not True:
            return []
        return [item for item in data["available"] if item in EFFORTS]
    except Exception:
        return []


def default_model(efforts, reason=None):
    """The CLI default model; a failed listing explains itself here."""
    return HarnessModel(
        id=DEFAULT_MODEL_ID,
        name="Antigravity default",
        description=reason or "",
        is_default=True,
        reasoning_efforts=tuple(efforts),
    )
