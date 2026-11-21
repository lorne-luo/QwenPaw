# -*- coding: utf-8 -*-
"""App-server reader robustness: one bad line or a dead reader must not
strand every session that shares the Codex process."""

# pylint: disable=protected-access

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.harness.codex.app_server import CodexAppServerClient

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


async def test_pending_approval_does_not_block_other_responses():
    client = CodexAppServerClient()
    client._send = AsyncMock()  # type: ignore[method-assign]
    release = asyncio.Event()

    async def handler(_message):
        await release.wait()
        return {"decision": "accept"}

    client.set_server_request_handler(handler)
    future = asyncio.get_running_loop().create_future()
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
    await asyncio.gather(*tuple(client._server_tasks))
    client._send.assert_awaited_once_with(
        {"id": 9, "result": {"decision": "accept"}},
    )


async def test_publish_delivers_synthetic_notification_to_subscribers():
    client = CodexAppServerClient()
    queue = client.subscribe()
    message = {
        "method": "qwenpaw/item/tool/requestUserInput",
        "params": {"threadId": "thread-1", "itemId": "question-1"},
    }

    client.publish(message)

    assert await asyncio.wait_for(queue.get(), timeout=1) == message


async def test_large_message_keeps_client_usable(tmp_path):
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


async def test_oversized_message_does_not_kill_reader(tmp_path, monkeypatch):
    # Shrink the real 128 MiB limit so the test does not stream that much.
    limit = 1024 * 1024
    monkeypatch.setattr(
        "plugins.harness.codex.app_server._STDIO_STREAM_LIMIT_BYTES",
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
        assert client._reader_alive()
    finally:
        await client.stop()


async def test_request_restarts_when_reader_is_gone(tmp_path):
    client = CodexAppServerClient(
        binary=_fake_server(tmp_path),
        environment={"FAKE_LINE_BYTES": "16"},
    )
    try:
        await client.start()
        first_pid = client._process.pid
        first_generation = client.generation
        client._reader_task.cancel()
        await asyncio.gather(client._reader_task, return_exceptions=True)
        assert client._process.returncode is None

        result = await asyncio.wait_for(
            client.request("thread/start", {}),
            timeout=15,
        )

        assert result == {"thread": {"id": "thread-1"}}
        assert client._process.pid != first_pid
        # Threads loaded into the old process must be resumed again.
        assert client.generation == first_generation + 1
    finally:
        await client.stop()


async def test_malformed_lines_do_not_kill_reader():
    # One bad line must not fail every session sharing this app-server.
    lines = [
        b"\xff\xfe not utf-8\n",
        b"[1, 2]\n",
        b'{"id": 5, "error": "plain string error"}\n',
        b'{"id": 6, "result": {"ok": true}}\n',
        b"",
    ]
    client = CodexAppServerClient()
    client._process = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=lines)),
    )
    loop = asyncio.get_running_loop()
    failed, ok = loop.create_future(), loop.create_future()
    client._pending[5] = failed
    client._pending[6] = ok

    await client._read_stdout()

    assert ok.result() == {"ok": True}
    with pytest.raises(Exception, match="plain string error"):
        failed.result()


async def test_line_over_stream_limit_is_skipped():
    lines = [
        ValueError("Separator is found, but chunk is longer than limit"),
        b'{"id": 7, "result": {"ok": true}}\n',
        b"",
    ]
    client = CodexAppServerClient()
    client._process = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=lines)),
    )
    ok = asyncio.get_running_loop().create_future()
    client._pending[7] = ok

    await client._read_stdout()

    assert ok.result() == {"ok": True}
