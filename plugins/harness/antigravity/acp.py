# -*- coding: utf-8 -*-
"""Minimal JSON-RPC 2.0 client for the ``agy acp`` stdio server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

LINE_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
PROTOCOL_VERSION = 1
# QwenPaw serves neither files nor terminals; agy uses its own tools.
CLIENT_CAPABILITIES = {
    "fs": {"readTextFile": False, "writeTextFile": False},
    "terminal": False,
}
# Pushed into a session queue once its prompt request has resolved.
PROMPT_DONE = object()

RequestHandler = Callable[[str, dict], Awaitable[dict]]


def redact(text):
    return re.sub(
        r"(?i)(bearer\s+|(?:token|password|api[_-]?key)\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        text,
    )


class AcpError(Exception):
    """A JSON-RPC error response or a transport failure."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code

    @property
    def auth_required(self):
        return "authentication required" in str(self).lower()


class AcpClient:
    """Own one ``agy acp`` process group and correlate its messages."""

    def __init__(self, process, on_request=None):
        self.process = process
        self.stderr = bytearray()
        self.agent: dict[str, Any] = {}
        self.closed = False
        self.error = AcpError("Antigravity ACP server exited")
        self._on_request = on_request
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._served: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self._drain_task = asyncio.create_task(self._drain())
        self._reader_task = asyncio.create_task(self._read())

    @classmethod
    async def start(cls, argv, cwd, env, on_request=None):
        """Spawn the server and complete the ``initialize`` handshake."""
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        client = cls(process, on_request)
        try:
            client.agent = await client.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientCapabilities": CLIENT_CAPABILITIES,
                    "clientInfo": {"name": "qwenpaw", "version": "1"},
                },
            )
            if (client.agent or {}).get("protocolVersion") != 1:
                raise AcpError(
                    "agy speaks an unsupported ACP protocol version; "
                    "upgrade Antigravity CLI",
                )
        except BaseException:
            await asyncio.shield(client.close())
            raise
        return client

    def subscribe(self, session_id):
        """Route this session's ``session/update`` payloads to a queue."""
        queue = asyncio.Queue()
        self._queues[session_id] = queue
        if self.closed:
            queue.put_nowait(None)
        return queue

    def unsubscribe(self, session_id):
        self._queues.pop(session_id, None)

    async def request(self, method, params):
        if self.closed:
            raise self.error
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
            )
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method, params):
        await self._send(
            {"jsonrpc": "2.0", "method": method, "params": params},
        )

    async def _send(self, message):
        if self.closed:
            raise self.error
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            try:
                self.process.stdin.write(data)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AcpError(
                    "Antigravity ACP server stopped accepting input",
                ) from exc

    async def _drain(self):
        while chunk := await self.process.stderr.read(8192):
            self.stderr.extend(chunk)
            if len(self.stderr) > STDERR_LIMIT:
                del self.stderr[:-STDERR_LIMIT]

    async def _read(self):
        reason = "Antigravity ACP server exited"
        try:
            while True:
                try:
                    line = await self.process.stdout.readline()
                except ValueError:
                    # asyncio dropped the oversized buffer; resynchronize.
                    logger.warning("Dropped an oversized Antigravity message")
                    continue
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.warning("Antigravity emitted invalid JSON")
                    continue
                if not isinstance(message, dict):
                    logger.warning("Antigravity emitted a non-object message")
                    continue
                self._dispatch(message)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Antigravity ACP reader failed")
            reason = f"Antigravity ACP reader failed: {exc}"
        finally:
            # Let the stderr tail arrive so the error can explain the exit.
            await asyncio.wait({self._drain_task}, timeout=0.5)
            detail = redact(self.stderr.decode(errors="replace")).strip()
            self._shutdown(
                AcpError(f"{reason}. {detail[-2000:]}" if detail else reason),
            )

    def _dispatch(self, message):
        method = message.get("method")
        if method is None:
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                return
            error = message.get("error")
            if error is not None:
                if not isinstance(error, dict):
                    error = {"message": str(error)}
                future.set_exception(
                    AcpError(
                        str(error.get("message") or "Unknown ACP error"),
                        error.get("code"),
                    ),
                )
            else:
                future.set_result(message.get("result"))
            return
        if "id" in message:
            task = asyncio.create_task(self._serve(message))
            self._served.add(task)
            task.add_done_callback(self._served.discard)
            return
        params = message.get("params")
        if method == "session/update" and isinstance(params, dict):
            queue = self._queues.get(params.get("sessionId"))
            update = params.get("update")
            if queue is not None and isinstance(update, dict):
                queue.put_nowait(update)

    async def _serve(self, message):
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            if self._on_request is None:
                raise AcpError(
                    f"Method not found: {message['method']}",
                    -32601,
                )
            params = message.get("params")
            reply["result"] = await self._on_request(
                message["method"],
                params if isinstance(params, dict) else {},
            )
        except AcpError as exc:
            reply["error"] = {"code": exc.code or -32603, "message": str(exc)}
        except Exception:  # pylint: disable=broad-except
            logger.exception("Antigravity client request handler failed")
            reply["error"] = {"code": -32603, "message": "Internal error"}
        try:
            await self._send(reply)
        except AcpError:
            pass

    def _shutdown(self, error):
        if not self.closed:
            self.closed = True
            self.error = error
        for future in self._pending.values():
            if not future.done():
                future.set_exception(self.error)
        for queue in self._queues.values():
            queue.put_nowait(None)

    async def close(self):
        """Reap the whole process group, its readers and pending calls."""
        self._shutdown(AcpError("Antigravity ACP server was closed"))
        process = self.process
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        # EOF on stdin first, then SIGTERM, each with a short grace period.
        for sig in (None, signal.SIGTERM):
            if process.returncode is not None:
                break
            if sig is not None:
                _killpg(process.pid, sig)
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                pass
        # Sweep the group: tool commands agy started share it.
        _killpg(process.pid, signal.SIGKILL)
        await process.wait()
        tasks = [self._reader_task, self._drain_task, *self._served]
        tasks = [task for task in tasks if task is not asyncio.current_task()]
        try:
            async with asyncio.timeout(1):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _killpg(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
