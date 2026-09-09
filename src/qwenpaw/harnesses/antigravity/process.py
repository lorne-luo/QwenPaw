# -*- coding: utf-8 -*-
"""Bounded NDJSON subprocess transport with process-group cleanup."""
import asyncio
import json
import os
import re
import signal

LINE_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 64 * 1024


def redact(text):
    return re.sub(
        r'(?i)(bearer\s+|(?:token|password|api[_-]?key)\s*[=:]\s*)\S+',
        r'\1[REDACTED]', text,
    )


class StreamProcess:
    def __init__(self, process):
        self.process = process
        self.stderr = bytearray()
        self.drain_task = asyncio.create_task(self._drain())

    @classmethod
    async def start(cls, argv, cwd, env):
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
        )
        return cls(process)

    async def _drain(self):
        while chunk := await self.process.stderr.read(8192):
            self.stderr.extend(chunk)
            if len(self.stderr) > STDERR_LIMIT:
                del self.stderr[:-STDERR_LIMIT]

    async def send(self, prompt):
        self.process.stdin.write((json.dumps({
            'event': 'user', 'message': {'content': prompt},
        }) + '\n').encode())
        await self.process.stdin.drain()

    async def read(self):
        try:
            line = await self.process.stdout.readline()
        except ValueError as exc:
            raise RuntimeError(
                'Antigravity NDJSON line exceeds 8 MiB',
            ) from exc
        if not line:
            detail = redact(self.stderr.decode(errors='replace'))
            raise RuntimeError('Antigravity EOF without result. ' + detail)
        if len(line) > LINE_LIMIT:
            raise RuntimeError('Antigravity NDJSON line exceeds 8 MiB')
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError('Antigravity returned invalid NDJSON') from exc
        if not isinstance(event, dict):
            raise RuntimeError('Antigravity returned a non-object event')
        return event

    async def close(self):
        pgid = self.process.pid
        for sig, timeout in ((signal.SIGINT, 3), (signal.SIGTERM, 2),
                             (signal.SIGKILL, 2)):
            if self.process.returncode is not None:
                break
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                break
            try:
                await asyncio.wait_for(self.process.wait(), timeout)
                break
            except asyncio.TimeoutError:
                continue
        if self.process.returncode is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not self.drain_task.done():
            try:
                async with asyncio.timeout(1):
                    await self.drain_task
            except (TimeoutError, asyncio.TimeoutError):
                self.drain_task.cancel()
                try:
                    await self.drain_task
                except asyncio.CancelledError:
                    pass
