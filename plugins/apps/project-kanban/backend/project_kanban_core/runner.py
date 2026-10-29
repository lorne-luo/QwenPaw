# -*- coding: utf-8 -*-
"""Run execution, event mapping, live stream and traces (§9.1, §10).

- A run is a detached ``asyncio.Task`` so ``/stop`` can cancel it.
- Every completed runtime event goes through :func:`map_event` exactly
  once; the mapped event is appended to the capped trace *and* sent to
  live subscribers, so live output and replay are identical (D7, D10).
- Each SSE connection gets its own ``SSEChannel``: the trace snapshot and
  the subscription happen without an ``await`` in between, so a viewer
  neither misses nor duplicates events, and several viewers can follow
  the same run.
- Agent-facing identity is pinned (``RUN_USER_ID`` / ``RUN_CHANNEL``) so
  runs, ``/result`` and session cleanup always address the same session
  file, whatever the request that triggered them.
"""

import asyncio
import dataclasses
import os
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from qwenpaw.pawapp.task import SSEChannel

from . import get_logger, models, rules
from .agents import AgentDirectory
from .store import Store

logger = get_logger(__name__)

RUN_USER_ID = "default"
RUN_CHANNEL = "console"

OUTCOME_RETRIES = 3
OUTCOME_RETRY_DELAY = 0.5

TOOL_CALL_TYPES = ("plugin_call", "function_call", "mcp_tool_call")
TOOL_OUTPUT_TYPES = tuple(t + "_output" for t in TOOL_CALL_TYPES)


# ── Event mapping (one mapper for live and replay) ───────────────────
def _plain(value: Any) -> Any:
    return getattr(value, "value", value)


def event_to_dict(event: Any) -> Optional[Dict[str, Any]]:
    """Normalise a runtime event (pydantic model, dataclass, dict)."""
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        try:
            return event.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - non-JSON extras
            return event.model_dump()
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return dataclasses.asdict(event)
    return None


def _tool_name(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    for block in content:
        data = block.get("data") if isinstance(block, dict) else None
        if isinstance(data, dict) and data.get("name"):
            return str(data["name"])
    return ""


def map_event(event: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Map a completed runtime message to a stream event, or ``None``.

    Output shapes (requirements R-SSE-2): ``message`` with ``text``,
    ``tool_start`` / ``tool_done`` with ``name``. The internal tool name
    ``assistant`` is skipped, as in the template.
    """
    if not isinstance(event, dict):
        return None
    if event.get("object") != "message":
        return None
    if _plain(event.get("status")) != "completed":
        return None
    kind = str(_plain(event.get("type")) or "")
    content = event.get("content") or []
    if kind == "message":
        if _plain(event.get("role")) not in (None, "assistant"):
            return None
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and _plain(block.get("type")) == "text"
            and block.get("text")
        ]
        text = "\n".join(texts)
        return {"type": "message", "text": text} if text else None
    if kind in TOOL_CALL_TYPES or kind in TOOL_OUTPUT_TYPES:
        name = _tool_name(content)
        if not name or name == "assistant":
            return None
        kind_out = "tool_done" if kind in TOOL_OUTPUT_TYPES else "tool_start"
        return {"type": kind_out, "name": name}
    return None


class RunFailed(Exception):
    """The host ended a stream with a failed/cancelled terminal envelope."""


def terminal_failure(event: Optional[Dict[str, Any]]) -> Optional[str]:
    """Error text if *event* is a terminal response that did not complete.

    Both the native runtime and third-party harnesses report errors as a
    normal last stream item (``object="response"``, ``status`` ``failed``
    or ``cancelled``, ``error={"code", "message"}``) instead of raising.
    """
    if not isinstance(event, dict) or event.get("object") != "response":
        return None
    status = _plain(event.get("status"))
    if status == "failed":
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return str(message or "Run failed")
    if status == "cancelled":
        return "Run cancelled by the host"
    return None


def trace_to_messages(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Render trace events in the ``/result`` message shape."""
    messages: List[Dict[str, Any]] = []
    for event in trace:
        if event["type"] == "message":
            messages.append(
                {
                    "role": "assistant",
                    "type": "message",
                    "content": [{"type": "text", "text": event["text"]}],
                },
            )
        elif event["type"] in ("tool_start", "tool_done"):
            kind = (
                "plugin_call_output"
                if event["type"] == "tool_done"
                else "plugin_call"
            )
            messages.append(
                {
                    "role": "assistant",
                    "type": kind,
                    "content": [
                        {"type": "data", "data": {"name": event["name"]}}
                    ],
                },
            )
    return messages


# ── Prompt (Appendix A) ──────────────────────────────────────────────
def build_prompt(
    project: Dict[str, Any],
    issue: Dict[str, Any],
    member_names: List[str],
) -> str:
    language = issue.get("language")
    key = rules.issue_key(project, issue)
    if language == "zh":
        return (
            f"你是项目「{project['name']}」({project['key']}) 的成员智能体。\n\n"
            "项目简介:\n"
            f"{project.get('description') or '(无简介)'}\n\n"
            f"项目成员: {', '.join(member_names)}\n\n"
            f"你被指派处理任务 {key}:\n"
            f"标题: {issue['title']}\n"
            f"描述: {issue.get('description') or '(无描述)'}\n\n"
            "如需了解本项目的其他任务及队友的处理结果，可调用\n"
            "project_kanban_list_project_issues，参数 "
            f'project="{project["key"]}"。\n\n'
            "请完成该任务，并用简洁的中文汇报你的处理结果。"
        )
    return (
        f'You are a member agent of the project "{project["name"]}" '
        f"({project['key']}).\n\n"
        "Project brief:\n"
        f"{project.get('description') or '(No brief)'}\n\n"
        f"Project members: {', '.join(member_names)}\n\n"
        f"You have been assigned issue {key}:\n"
        f"Title: {issue['title']}\n"
        f"Description: {issue.get('description') or '(No description)'}\n\n"
        "To see the other issues of this project and your teammates' "
        "results, call\n"
        "project_kanban_list_project_issues with "
        f'project="{project["key"]}".\n\n'
        "Complete the issue and report the result concisely in English."
    )


def agent_ctx(base_ctx: Any, agent_id: str) -> Any:
    """Copy *base_ctx* for *agent_id* with the pinned run identity."""
    return dataclasses.replace(
        base_ctx,
        agent_id=agent_id,
        user_id=RUN_USER_ID,
        channel=RUN_CHANNEL,
    )


# ── Runner ───────────────────────────────────────────────────────────
class Runner:
    """Owns run tasks, live traces and SSE subscribers (process memory)."""

    def __init__(self, store: Store, agents: AgentDirectory) -> None:
        self.store = store
        self.agents = agents
        self.on_finished: Callable[[], None] = lambda: None
        self._tasks: Dict[str, "asyncio.Task[None]"] = {}
        self._task_agent: Dict[str, str] = {}
        # Cancelled runs still unwinding (task -> agent): the agent stays
        # busy until the host's chat stream has really stopped.
        self._unwinding: Dict["asyncio.Task[None]", str] = {}
        self._traces: Dict[str, Deque[Dict[str, Any]]] = {}
        self._subscribers: Dict[str, Set[SSEChannel]] = {}

    # ── State ────────────────────────────────────────────────────────
    def is_live(self, issue_id: str) -> bool:
        task = self._tasks.get(issue_id)
        return task is not None and not task.done()

    def live_ids(self) -> Set[str]:
        return {iid for iid in list(self._tasks) if self.is_live(iid)}

    def agent_running(self, agent_id: str) -> bool:
        if agent_id in self._unwinding.values():
            return True
        return any(
            self._task_agent.get(iid) == agent_id for iid in self.live_ids()
        )

    def trace(self, issue_id: str) -> List[Dict[str, Any]]:
        return list(self._traces.get(issue_id, ()))

    def _is_current(self, issue_id: str) -> bool:
        """Whether the calling task is still the live run of *issue_id*.

        ``stop`` (``/stop``, ``cancel_all`` at shutdown) and ``launch``
        drop a task from ``_tasks`` before cancelling it. The host
        swallows that cancel and ends the stream normally, so a dropped
        run keeps going for a while: it must not write an outcome or touch
        the issue's stream, which may already belong to a new run.
        """
        return self._tasks.get(issue_id) is asyncio.current_task()

    # ── Launch / stop ────────────────────────────────────────────────
    def launch(
        self,
        base_ctx: Any,
        info: Dict[str, Any],
        prompt: str,
        log_key: str = "",
    ) -> "asyncio.Task[None]":
        issue_id = info["issue_id"]
        self._end_stream(issue_id)
        old = self._tasks.pop(issue_id, None)
        if old is not None and not old.done():
            old.cancel()
        self._traces[issue_id] = deque(maxlen=models.TRACE_MAX)
        task = asyncio.create_task(
            self._execute(base_ctx, info, prompt, log_key or issue_id),
            name=f"project-kanban-run-{issue_id}",
        )
        self._tasks[issue_id] = task
        self._task_agent[issue_id] = info["agent_id"]

        def _forget(done: "asyncio.Task[None]", _id: str = issue_id) -> None:
            if self._tasks.get(_id) is done:
                self._tasks.pop(_id, None)
                self._task_agent.pop(_id, None)
            if self._unwinding.pop(done, None) is not None:
                self.on_finished()  # the agent is free only now

        task.add_done_callback(_forget)
        return task

    def stop(self, issue_id: str) -> bool:
        """Cancel a live run and close its stream; ``True`` if one ran."""
        task = self._tasks.pop(issue_id, None)
        agent_id = self._task_agent.pop(issue_id, None)
        live = task is not None and not task.done()
        if live:
            self._unwinding[task] = agent_id or ""
            task.cancel()
        self._end_stream(issue_id)
        return live

    def cancel_all(self) -> None:
        for issue_id in list(self._tasks):
            self.stop(issue_id)

    async def _execute(
        self,
        base_ctx: Any,
        info: Dict[str, Any],
        prompt: str,
        log_key: str,
    ) -> None:
        issue_id = info["issue_id"]
        agent_id = info["agent_id"]
        session_id = info["session_id"]
        logger.info(
            "[project-kanban] run start project=%s issue=%s agent=%s",
            info["project_id"],
            log_key,
            agent_id,
        )
        error: Optional[str] = None
        excerpt: Optional[str] = None
        try:
            previous = info.get("previous_agent_id") or ""
            if previous and previous != agent_id:
                await self.delete_session(base_ctx, previous, session_id)
            if self.agents.exists(agent_id) is False:
                raise RuntimeError(f"Agent {agent_id} not found")
            ctx = agent_ctx(base_ctx, agent_id)
            await self._consume(
                ctx.chat_stream("/clear", session_id=session_id),
                issue_id,
                log_key,
                publish=False,
            )
            excerpt = await self._consume(
                ctx.chat_stream(prompt, session_id=session_id),
                issue_id,
                log_key,
                publish=True,
            )
        # CancelledError (/stop or shutdown) is not caught here: the stop
        # handler owns the status change and no outcome is written.
        except RunFailed as exc:
            error = str(exc)
            logger.error(
                "[project-kanban] run failed project=%s issue=%s agent=%s: %s",
                info["project_id"],
                log_key,
                agent_id,
                error,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or exc.__class__.__name__
            logger.error(
                "[project-kanban] run failed project=%s issue=%s agent=%s: %s",
                info["project_id"],
                log_key,
                agent_id,
                error,
                exc_info=True,
            )

        if not self._is_current(issue_id):
            # Stopped or shut down: the stop wrote the outcome, or orphan
            # recovery re-queues the issue at the next launch.
            logger.info(
                "[project-kanban] run stopped project=%s issue=%s agent=%s",
                info["project_id"],
                log_key,
                agent_id,
            )
        else:
            outcome = "failed" if error else "succeeded"
            await self._record_outcome(info, outcome, error, excerpt, log_key)
            logger.info(
                "[project-kanban] run %s project=%s issue=%s agent=%s",
                outcome,
                info["project_id"],
                log_key,
                agent_id,
            )
            # A stop during the write may already have handed the stream to
            # a new run.
            if self._is_current(issue_id):
                final = (
                    {"type": "error", "message": error}
                    if error
                    else {"type": "done"}
                )
                await self._publish(issue_id, final, record=False)
                self._end_stream(issue_id)
        self.on_finished()

    async def _consume(
        self,
        stream: Any,
        issue_id: str,
        log_key: str,
        publish: bool,
    ) -> Optional[str]:
        """Read one host stream; return the last assistant text.

        Raises :class:`RunFailed` as soon as the host reports a failed or
        cancelled terminal envelope, and always closes the stream.
        """
        excerpt: Optional[str] = None
        try:
            async for event in stream:
                if getattr(event, "delta", False):
                    continue
                data = event_to_dict(event)
                failure = terminal_failure(data)
                if failure is not None:
                    raise RunFailed(failure)
                if not publish or not self._is_current(issue_id):
                    continue
                mapped = map_event(data)
                if mapped is None:
                    continue
                logger.debug(
                    "[project-kanban] event issue=%s %s",
                    log_key,
                    mapped["type"],
                )
                if mapped["type"] == "message":
                    excerpt = mapped["text"]
                await self._publish(issue_id, mapped)
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        return excerpt

    async def _record_outcome(
        self,
        info: Dict[str, Any],
        outcome: str,
        error: Optional[str],
        excerpt: Optional[str],
        log_key: str,
    ) -> None:
        """Write the outcome; retry briefly on storage errors."""
        for attempt in range(OUTCOME_RETRIES):
            try:
                async with self.store.txn() as board:
                    rules.finish_run(
                        board,
                        info["issue_id"],
                        info["started_at"],
                        outcome,
                        error,
                        excerpt,
                        time.time(),
                    )
                return
            except Exception:  # noqa: BLE001
                if attempt + 1 == OUTCOME_RETRIES:
                    logger.exception(
                        "[project-kanban] could not record outcome "
                        "issue=%s agent=%s; it stays in_progress until "
                        "stopped or the app restarts",
                        log_key,
                        info["agent_id"],
                    )
                    return
                await asyncio.sleep(OUTCOME_RETRY_DELAY * (attempt + 1))

    # ── Stream ───────────────────────────────────────────────────────
    async def _publish(
        self,
        issue_id: str,
        event: Dict[str, Any],
        record: bool = True,
    ) -> None:
        if record:
            trace = self._traces.get(issue_id)
            if trace is not None:
                trace.append(event)
        for channel in list(self._subscribers.get(issue_id, ())):
            await channel.send_event(event)

    def subscribe(
        self,
        issue_id: str,
    ) -> Tuple[List[Dict[str, Any]], Optional[SSEChannel]]:
        """Return ``(replay, channel)``; ``channel`` is ``None`` if idle."""
        replay = self.trace(issue_id)
        if not self.is_live(issue_id):
            return replay, None
        channel = SSEChannel()
        self._subscribers.setdefault(issue_id, set()).add(channel)
        return replay, channel

    def unsubscribe(self, issue_id: str, channel: SSEChannel) -> None:
        subscribers = self._subscribers.get(issue_id)
        if subscribers is not None:
            subscribers.discard(channel)

    def _end_stream(self, issue_id: str) -> None:
        for channel in self._subscribers.pop(issue_id, set()):
            channel.close()
        self._traces.pop(issue_id, None)

    # ── Session access ───────────────────────────────────────────────
    async def delete_session(
        self,
        base_ctx: Any,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Best effort: delete *agent_id*'s session file for *session_id*."""
        try:
            ctx = agent_ctx(base_ctx, agent_id)
            # pylint: disable=protected-access
            workspace = await ctx._get_workspace()
            session = getattr(workspace, "session", None)
            if session is None:
                return False
            path = session._get_save_path(
                session_id=session_id,
                user_id=RUN_USER_ID,
                channel=RUN_CHANNEL,
            )
            await asyncio.to_thread(os.remove, path)
            logger.info(
                "[project-kanban] deleted session %s of agent %s",
                session_id,
                agent_id,
            )
            return True
        except FileNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            logger.warning(
                "[project-kanban] could not delete session %s of agent %s",
                session_id,
                agent_id,
                exc_info=True,
            )
            return False

    async def result(
        self, base_ctx: Any, issue: Dict[str, Any]
    ) -> Dict[str, Any]:
        """``/result`` (§10.2): session of ``last_run.agent_id`` or trace."""
        run = issue.get("last_run")
        if not run:
            return {"messages": []}
        if run.get("outcome") == "failed":
            return {"error": run.get("error") or "Run failed"}
        messages: List[Dict[str, Any]] = []
        if base_ctx is not None:
            try:
                ctx = agent_ctx(base_ctx, run["agent_id"])
                history = await ctx.get_session_history(
                    session_id=run["session_id"],
                )
                messages = [
                    m for m in history or [] if m.get("role") == "assistant"
                ]
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[project-kanban] session history failed issue=%s",
                    issue["id"],
                    exc_info=True,
                )
        if not messages and issue.get("status") == "in_progress":
            messages = trace_to_messages(self.trace(issue["id"]))
        return {"messages": messages}
