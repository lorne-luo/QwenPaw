# -*- coding: utf-8 -*-
"""The optional user-input hook: the gateway hands a console answer to the
live backend instance whose turn is waiting on it."""

# pylint: disable=protected-access

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.plugins.testing import PluginTestHost

SOURCE = """
import asyncio
from qwenpaw.plugins.harnesses import (
    HarnessCapabilities, HarnessEvent, HarnessEventKind as Kind, HarnessSpec,
)

class Backend:
    def __init__(self, context, settings):
        self.waiting, self.answered = asyncio.Event(), asyncio.Event()
        self.resolved = []

    async def run_turn(self, context, request):
        self.waiting.set()
        await self.answered.wait()
        yield HarnessEvent(Kind.TEXT_DELTA, text=self.resolved[-1][2])
        yield HarnessEvent(Kind.COMPLETED)
RESOLVE
    async def close(self):
        pass

class Plugin:
    def register(self, context):
        self.created = []
        def create(factory_context, settings):
            backend = Backend(factory_context, settings)
            self.created.append(backend)
            return backend
        context.register_harness(
            HarnessSpec("codex", "codex", create, HarnessCapabilities()),
        )

plugin = Plugin()
"""

RESOLVE = """
    async def resolve_user_input(self, context, *, request_id, answers):
        if request_id == "gone":
            raise LookupError(request_id)
        if context.session_id != "chat":
            raise PermissionError(request_id)
        self.resolved.append(
            (context.session_id, request_id, answers["q"]["answers"][0]),
        )
        self.answered.set()
"""


def package(tmp_path, backend, *, supported):
    root = tmp_path / ("source-" + backend)
    root.mkdir()
    source = SOURCE.replace("RESOLVE", RESOLVE if supported else "")
    (root / "main.py").write_text(
        source.replace('"codex"', repr(backend)),
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "id": "qwenpaw-harness-" + backend,
                "version": "1.0.0",
                "entry": {"backend": "main.py"},
                "sdk": {
                    "version": "1.1.0",
                    "capabilities": ["harnesses", "storage", "resources"],
                },
                "meta": {"harnesses": [{"id": backend, "label": backend}]},
            },
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture(name="host")
async def make_host(tmp_path, monkeypatch):
    async with PluginTestHost(tmp_path / "host") as test_host:
        assert (
            await test_host.install(package(tmp_path, "codex", supported=True))
        ).ok
        assert (
            await test_host.install(
                package(tmp_path, "qoder", supported=False),
            )
        ).ok
        workspace = await test_host._workspace("test")
        monkeypatch.setattr(
            "qwenpaw.app.agent_context.get_agent_for_request",
            AsyncMock(return_value=workspace),
        )
        test_host.backend = "codex"
        monkeypatch.setattr(
            "qwenpaw.config.config.load_agent_config_async",
            AsyncMock(
                side_effect=lambda _agent_id: SimpleNamespace(
                    backend=test_host.backend,
                    backend_settings={},
                ),
            ),
        )
        yield test_host


def created(host, backend="codex"):
    plugin_id = "qwenpaw-harness-" + backend
    return host._loader.get_loaded_plugin(plugin_id).instance.created


def answer(host, request_id, session_id="chat", backend="codex"):
    return host.request(
        "POST",
        f"/api/harnesses/{backend}/user-input/{request_id}",
        json={"session_id": session_id, "answers": {"q": {"answers": ["B"]}}},
    )


async def test_answer_reaches_the_live_instance_of_a_waiting_turn(host):
    from qwenpaw.schemas import AgentRequest

    runtime = (await host._workspace("test")).backend_runtime
    request = AgentRequest(
        session_id="chat",
        user_id="alice",
        channel="console",
        input=[{"role": "user", "content": [{"type": "text", "text": "q"}]}],
    )

    async def turn():
        return [item async for item in runtime.stream("codex", request)]

    task = asyncio.create_task(turn())
    try:
        async with asyncio.timeout(5):
            while not (created(host) and created(host)[0].waiting.is_set()):
                await asyncio.sleep(0.005)
        response = await answer(host, "question-1")
        assert response.status == 200, response.text
        assert response.json() == {"ok": True}
        items = await asyncio.wait_for(task, 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # The busy instance was reused, not replaced or rebuilt.
    assert len(created(host)) == 1
    assert created(host)[0].resolved == [("chat", "question-1", "B")]
    assert items[-1].status == "completed"


@pytest.mark.parametrize(
    ("request_id", "session_id", "status", "code"),
    [
        ("gone", "chat", 404, "user_input_expired"),
        ("question-1", "other", 409, "user_input_session_mismatch"),
    ],
)
async def test_backend_errors_map_to_expired_and_session_mismatch(
    host,
    request_id,
    session_id,
    status,
    code,
):
    response = await answer(host, request_id, session_id)
    assert response.status == status, response.text
    assert response.json()["detail"]["code"] == code


async def test_backend_without_the_hook_is_unsupported(host):
    host.backend = "qoder"
    response = await answer(host, "question-1", backend="qoder")
    assert response.status == 409, response.text
    assert response.json()["detail"]["code"] == (
        "resolve_user_input_not_supported"
    )


async def test_answer_requires_a_session(host):
    from qwenpaw.plugins import SDKError

    response = await answer(host, "question-1", session_id="")
    assert response.status == 422
    runtime = (await host._workspace("test")).backend_runtime
    with pytest.raises(SDKError, match="requires a session ID"):
        await runtime.call(
            "codex",
            "resolve_user_input",
            request_id="question-1",
            answers={},
        )
