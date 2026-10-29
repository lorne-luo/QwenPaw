# -*- coding: utf-8 -*-
"""Scheduling: per-agent FIFO across projects, wake-up, orphans (§9.4).

- The agent is the concurrency unit: one running issue per agent across
  all projects (I1). Busy is read from the board, so it holds across
  processes sharing ``board.json``.
- ``wake()`` sets an ``asyncio.Event``; the loop also sweeps every 30 s.
  Passes are serialised by the single loop task.
- ``base_ctx`` is built at launch from the host app state (Q1) and then
  replaced by the most recent request ctx (R-DSP-7).
"""

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from . import approvals, get_logger, rules
from .agents import AgentDirectory
from .runner import Runner, build_prompt
from .store import Store

logger = get_logger(__name__)

SWEEP_INTERVAL = 30.0


class Dispatcher:
    def __init__(
        self,
        store: Store,
        runner: Runner,
        agents: AgentDirectory,
        approval_api: Callable[[], Optional[approvals.ApprovalApi]] = (
            approvals.load_api
        ),
        sweep_interval: float = SWEEP_INTERVAL,
    ) -> None:
        self.store = store
        self.runner = runner
        self.agents = agents
        self.approval_api = approval_api
        self.sweep_interval = sweep_interval
        self.base_ctx: Any = None
        self._event: Optional[asyncio.Event] = None
        self._task: Optional["asyncio.Task[None]"] = None
        self._pass_lock = asyncio.Lock()
        runner.on_finished = self.wake

    # ── Lifecycle ────────────────────────────────────────────────────
    def capture(self, ctx: Any) -> None:
        """Remember the most recent request ctx; wake on the first one."""
        if ctx is None:
            return
        first = self.base_ctx is None
        self.base_ctx = ctx
        if first:
            self.wake()

    def wake(self) -> None:
        if self._event is not None:
            self._event.set()

    async def start(self, launch_ctx: Any = None) -> None:
        """Recover orphans, start the loop, dispatch what is eligible."""
        try:
            await self.recover_orphans()
        except Exception:  # noqa: BLE001 - board unavailable: keep serving
            logger.exception("[project-kanban] orphan recovery failed")
        if launch_ctx is not None and self.base_ctx is None:
            self.base_ctx = launch_ctx
        self._event = asyncio.Event()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(),
                name="project-kanban-dispatcher",
            )
        self.wake()
        logger.info(
            "[project-kanban] dispatcher started (base ctx %s)",
            "ready" if self.base_ctx is not None else "waits for a request",
        )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.runner.cancel_all()
        logger.info("[project-kanban] dispatcher stopped")

    async def _loop(self) -> None:
        assert self._event is not None
        while True:
            # Not wait_for: on Python 3.11 it swallows a cancel that lands
            # as the event fires, and stop() would await this loop forever.
            try:
                async with asyncio.timeout(self.sweep_interval):
                    await self._event.wait()
            except asyncio.TimeoutError:
                pass
            self._event.clear()
            try:
                await self.run_pass()
            except Exception:  # noqa: BLE001
                logger.exception("[project-kanban] dispatcher pass failed")

    # ── Orphans ──────────────────────────────────────────────────────
    async def recover_orphans(self) -> int:
        async with self.store.txn() as board:
            count = rules.recover_orphans(
                board,
                self.runner.live_ids(),
                time.time(),
            )
        if count:
            logger.info(
                "[project-kanban] re-queued %d interrupted run(s)", count
            )
        return count

    # ── Dispatch ─────────────────────────────────────────────────────
    def _agent_busy(self, board: Dict[str, Any], agent_id: str) -> bool:
        """The single busy rule used inside every start transaction.

        Busy = a running issue on the board (any project, any process) or
        a run task of this process that is live or still unwinding after
        a stop (the board already says ``backlog`` for the latter). It is
        evaluated inside the transaction, and the launch follows the
        commit without an ``await``, so check-and-start is atomic for
        every coroutine of this process.
        """
        return rules.running_issue_of(
            board, agent_id
        ) is not None or self.runner.agent_running(agent_id)

    async def run_pass(self) -> int:
        """One pass: start the head of each idle agent's queue."""
        async with self._pass_lock:
            ctx = self.base_ctx
            if ctx is None:
                return 0
            board = self.store.read()
            busy = {
                i["assignee"]
                for i in board["issues"]
                if i["status"] == "in_progress"
            }
            launched = 0
            for agent_id in list(rules.agent_queues(board)):
                if agent_id in busy or self.runner.agent_running(agent_id):
                    continue
                launch = None
                async with self.store.txn() as fresh:
                    queue = rules.agent_queues(fresh).get(agent_id)
                    if queue and not self._agent_busy(fresh, agent_id):
                        issue = queue[0]
                        info = rules.start_run(issue, time.time())
                        project = rules.find_project(
                            fresh, issue["project_id"]
                        )
                        launch = (info, project, issue)
                if launch is not None:
                    self._launch(ctx, *launch)
                    launched += 1
            return launched

    def _launch(
        self,
        ctx: Any,
        info: Dict[str, Any],
        project: Dict[str, Any],
        issue: Dict[str, Any],
    ) -> None:
        names = []
        for agent_id in rules.member_ids(project):
            name = self.agents.display_name(agent_id)
            names.append(name if name == agent_id else f"{name} ({agent_id})")
        key = rules.issue_key(project, issue)
        logger.info(
            "[project-kanban] dispatch project=%s issue=%s agent=%s",
            project["id"],
            key,
            info["agent_id"],
        )
        self.runner.launch(ctx, info, build_prompt(project, issue, names), key)

    async def run_issue(
        self,
        ctx: Any,
        project_id: str,
        issue_id: str,
        language: Optional[str],
    ) -> Dict[str, Any]:
        """``POST …/run`` (R-DSP-5)."""
        self.capture(ctx)
        async with self.store.txn() as board:
            issue, info = rules.plan_run(
                board,
                project_id,
                issue_id,
                language,
                time.time(),
                agent_busy=lambda agent_id: self._agent_busy(board, agent_id),
            )
            project = rules.find_project(board, project_id)
            position = None if info else rules.queue_position(board, issue)
        if info is not None:
            self._launch(self.base_ctx, info, project, issue)
        else:
            logger.info(
                "[project-kanban] queued project=%s issue=%s agent=%s pos=%s",
                project_id,
                rules.issue_key(project, issue),
                issue["assignee"],
                position,
            )
        return {
            "issue": rules.issue_view(project, issue),
            "started": info is not None,
            "queue_position": position,
        }

    async def stop_issue(
        self, project_id: str, issue_id: str
    ) -> Dict[str, Any]:
        """``POST …/stop`` (§9.5).

        The task is cancelled *after* the transaction: a run that a
        concurrent pass has just flipped to ``in_progress`` is launched
        before this transaction can take the lock, so it is always found.
        """
        async with self.store.txn() as board:
            issue, stopped = rules.stop_run(
                board,
                project_id,
                issue_id,
                time.time(),
            )
            project = rules.find_project(board, project_id)
        if stopped:
            self.runner.stop(issue_id)
            self.wake()
            logger.info(
                "[project-kanban] stopped project=%s issue=%s agent=%s",
                project_id,
                rules.issue_key(project, issue),
                issue["assignee"],
            )
            try:
                api = self.approval_api()
            except Exception:  # noqa: BLE001 - best effort
                api = None
            await approvals.cancel_for_session(
                api,
                rules.session_id_for(issue_id),
            )
        return rules.issue_view(project, issue)
