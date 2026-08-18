# -*- coding: utf-8 -*-
"""Tests for Codex app-server JSON-RPC request handling."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.harnesses.codex.app_server import (
    CodexAppServerClient,
    _STDIO_STREAM_LIMIT_BYTES,
)

FAKE_SERVER = """
import json
import os
import sys

size = int(os.environ["FAKE_LINE_BYTES"])
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    request_id = message.get("id")
    if message.get("method") == "initialize":
        sys.stdout.write(json.dumps({"id": request_id, "result": {}}) + "\\n")
        sys.stdout.flush()
        payload = {"method": "thread/item", "params": {"blob": "x" * size}}
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()
        continue
    if request_id is not None:
        result = {"thread": {"id": "thread-1"}}
        payload = json.dumps({"id": request_id, "result": result})
        sys.stdout.write(payload + "\\n")
        sys.stdout.flush()
"""


def _fake_server(tmp_path: Path) -> str:
    script = tmp_path / "fake_app_server.py"
    script.write_text(
        f"#!{sys.executable}\n{FAKE_SERVER}",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


@pytest.mark.asyncio
async def test_server_request_handler_returns_decision() -> None:
    client = CodexAppServerClient()
    client._send = AsyncMock()  # type: ignore[method-assign]
    handler = AsyncMock(return_value={"decision": "accept"})
    client.set_server_request_handler(handler)
    message = {
        "id": 7,
        "method": "item/commandExecution/requestApproval",
        "params": {"command": "pytest"},
    }

    await client._dispatch(message)
    await asyncio.gather(*tuple(client._server_request_tasks))

    handler.assert_awaited_once_with(message)
    client._send.assert_awaited_once_with(
        {"id": 7, "result": {"decision": "accept"}},
    )


@pytest.mark.asyncio
async def test_app_server_uses_large_bounded_stdio_limit(monkeypatch) -> None:
    stream = SimpleNamespace(readline=AsyncMock(return_value=b""))
    process = SimpleNamespace(
        returncode=None,
        stdin=SimpleNamespace(),
        stdout=stream,
        stderr=stream,
    )
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "qwenpaw.harnesses.codex.app_server.resolve_codex_binary_info",
        lambda _binary: SimpleNamespace(path=Path("/tmp/codex")),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    client = CodexAppServerClient()
    client.request = AsyncMock(return_value={})  # type: ignore[method-assign]
    client.notify = AsyncMock()  # type: ignore[method-assign]

    await client.start()
    await asyncio.sleep(0)

    assert create.await_args.kwargs["limit"] == _STDIO_STREAM_LIMIT_BYTES


@pytest.mark.asyncio
async def test_pending_approval_does_not_block_other_responses() -> None:
    client = CodexAppServerClient()
    client._send = AsyncMock()  # type: ignore[method-assign]
    release = asyncio.Event()

    async def handler(_message: dict[str, object]) -> dict[str, object]:
        await release.wait()
        return {"decision": "accept"}

    client.set_server_request_handler(handler)
    future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
    client._pending[42] = future

    await client._dispatch(
        {
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "ls"},
        },
    )
    await client._dispatch({"id": 42, "result": {"ok": True}})

    assert await asyncio.wait_for(future, timeout=1) == {"ok": True}
    release.set()
    await asyncio.gather(*tuple(client._server_request_tasks))


@pytest.mark.asyncio
async def test_publish_delivers_synthetic_notification_to_subscribers(
) -> None:
    client = CodexAppServerClient()
    queue = client.subscribe()
    message = {
        "method": "qwenpaw/item/tool/requestUserInput",
        "params": {"threadId": "thread-1", "itemId": "question-1"},
    }

    client.publish(message)

    assert await asyncio.wait_for(queue.get(), timeout=1) == message


@pytest.mark.asyncio
async def test_large_message_keeps_client_usable(tmp_path: Path) -> None:
    client = CodexAppServerClient(
        binary=_fake_server(tmp_path),
        environment={"FAKE_LINE_BYTES": "200000"},
    )
    try:
        await client.start()
        queue = client.subscribe()
        message = await asyncio.wait_for(queue.get(), timeout=5)
        assert len(message["params"]["blob"]) == 200000
        result = await asyncio.wait_for(
            client.request("thread/start", {}),
            timeout=5,
        )
        assert result == {"thread": {"id": "thread-1"}}
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_oversized_message_does_not_kill_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Shrink the real 128 MiB limit so the test does not stream that much.
    limit = 1024 * 1024
    monkeypatch.setattr(
        "qwenpaw.harnesses.codex.app_server._STDIO_STREAM_LIMIT_BYTES",
        limit,
    )
    client = CodexAppServerClient(
        binary=_fake_server(tmp_path),
        environment={"FAKE_LINE_BYTES": str(limit + 1024)},
    )
    try:
        await client.start()
        result = await asyncio.wait_for(
            client.request("thread/start", {}),
            timeout=15,
        )
        assert result == {"thread": {"id": "thread-1"}}
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_request_restarts_when_reader_is_gone(tmp_path: Path) -> None:
    client = CodexAppServerClient(
        binary=_fake_server(tmp_path),
        environment={"FAKE_LINE_BYTES": "16"},
    )
    try:
        await client.start()
        first_pid = client._process.pid  # type: ignore[union-attr]
        client._reader_task.cancel()  # type: ignore[union-attr]
        await asyncio.gather(
            client._reader_task,  # type: ignore[arg-type]
            return_exceptions=True,
        )

        result = await asyncio.wait_for(
            client.request("thread/start", {}),
            timeout=15,
        )

        assert result == {"thread": {"id": "thread-1"}}
        assert client._process.pid != first_pid  # type: ignore[union-attr]
    finally:
        await client.stop()
