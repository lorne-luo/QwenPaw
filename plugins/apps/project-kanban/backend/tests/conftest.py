# -*- coding: utf-8 -*-
"""Shared fixtures for Project Kanban backend tests."""

import asyncio
import dataclasses
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import pytest

# Isolate qwenpaw's WORKING_DIR before the first qwenpaw import so nothing
# touches the developer's ~/.qwenpaw.
os.environ.setdefault(
    "QWENPAW_WORKING_DIR",
    tempfile.mkdtemp(prefix="project-kanban-tests-"),
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# pylint: disable=wrong-import-position
from project_kanban_core import models, rules  # noqa: E402
from project_kanban_core.agents import AgentDirectory  # noqa: E402
from project_kanban_core.dispatcher import Dispatcher  # noqa: E402
from project_kanban_core.runner import Runner  # noqa: E402
from project_kanban_core.store import Store  # noqa: E402

NOW = 1_761_400_000.0


class FakeAgents(AgentDirectory):
    """Agent directory with a fixed set of ids (``None`` = unknown)."""

    def __init__(self, ids: Optional[List[str]] = None) -> None:
        self.ids = ids

    def known_ids(self):  # type: ignore[override]
        return None if self.ids is None else set(self.ids)

    def display_name(self, agent_id: str) -> str:
        return agent_id.capitalize()


def message_event(text: str) -> Dict[str, Any]:
    return {
        "object": "message",
        "status": "completed",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }


def response_event(status: str, message: str = "") -> Dict[str, Any]:
    """Terminal envelope as the host runtimes emit it (no exception)."""
    event = {"object": "response", "status": status, "output": []}
    if message:
        event["error"] = {"code": "harness_error", "message": message}
    return event


def tool_event(name: str, output: bool = False) -> Dict[str, Any]:
    return {
        "object": "message",
        "status": "completed",
        "type": "plugin_call_output" if output else "plugin_call",
        "role": "tool" if output else "assistant",
        "content": [{"type": "data", "data": {"name": name}}],
    }


@dataclasses.dataclass
class FakeCtx:
    """Stand-in for ``PawAppContext`` with a scripted ``chat_stream``.

    ``script(agent_id, message)`` returns the events to yield; a coroutine
    function ``gate(agent_id)`` (if set) is awaited before the events so a
    test can hold a run open.
    """

    app_id: str = "project-kanban"
    agent_id: str = "default"
    channel: str = "console"
    user_id: str = "default"
    hub: Any = None

    async def chat_stream(
        self,
        message: str,
        *,
        session_id: str = "",
        **_: Any,
    ) -> AsyncIterator[Any]:
        self.hub.calls.append((self.agent_id, message, session_id))
        try:
            if message == "/clear":
                for event in self.hub.clear_script(self.agent_id):
                    yield event
                return
            if self.hub.gate is not None:
                await self.hub.gate(self.agent_id)
            for event in self.hub.script(self.agent_id, message):
                if isinstance(event, Exception):
                    raise event
                self.hub.yielded.append((self.agent_id, message, event))
                yield event
        finally:
            self.hub.closed.append((self.agent_id, message))

    async def get_session_history(self, session_id: str = "") -> List[Any]:
        return self.hub.history.get((self.agent_id, session_id), [])

    async def _get_workspace(self) -> Any:
        return self.hub.workspaces.get(self.agent_id)


class Hub:
    """Shared state behind every ``FakeCtx`` copy."""

    def __init__(self) -> None:
        self.calls: List[Any] = []
        self.history: Dict[Any, List[Any]] = {}
        self.workspaces: Dict[str, Any] = {}
        self.gate: Optional[Callable[[str], Any]] = None
        self.clear_script: Callable[[str], List[Any]] = lambda agent: []
        self.yielded: List[Any] = []
        self.closed: List[Any] = []
        self.script: Callable[[str, str], List[Any]] = lambda agent, msg: [
            message_event(f"done by {agent}"),
        ]

    def ctx(self) -> FakeCtx:
        return FakeCtx(hub=self)


class Gate:
    """Per-agent release switch for holding runs open."""

    def __init__(self) -> None:
        self.events: Dict[str, asyncio.Event] = {}

    def _event(self, agent_id: str) -> asyncio.Event:
        return self.events.setdefault(agent_id, asyncio.Event())

    async def __call__(self, agent_id: str) -> None:
        await self._event(agent_id).wait()

    def release(self, agent_id: str) -> None:
        self._event(agent_id).set()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


@pytest.fixture
def board() -> Dict[str, Any]:
    return {"schema_version": 1, "projects": [], "issues": []}


@pytest.fixture
def hub() -> Hub:
    return Hub()


@pytest.fixture
def engine(store: Store, hub: Hub):
    """A dispatcher wired to a store, runner and fake agents."""
    agents = FakeAgents(["alice", "bob", "carol"])
    runner = Runner(store, agents)
    dispatcher = Dispatcher(store, runner, agents, approval_api=lambda: None)
    yield dispatcher
    runner.cancel_all()


def make_project(
    board: Dict[str, Any],
    name: str = "Website rebuild",
    key: Optional[str] = None,
    members: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return rules.create_project(
        board,
        models.ProjectCreate(name=name, key=key, members=members or []),
        NOW,
        lambda _a: True,
    )


def make_issue(
    board: Dict[str, Any],
    project: Dict[str, Any],
    title: str = "Task",
    assignee: str = "",
    status: str = "backlog",
    now: float = NOW,
) -> Dict[str, Any]:
    return rules.create_issue(
        board,
        project["id"],
        models.IssueCreate(title=title, assignee=assignee, status=status),
        now,
    )


async def wait_until(
    predicate: Callable[[], bool], timeout: float = 2.0
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
