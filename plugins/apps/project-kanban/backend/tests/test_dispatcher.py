# -*- coding: utf-8 -*-
"""Scheduling (§9): busy rule, FIFO across projects, wake-up, orphans."""

import asyncio
import time

import pytest

from conftest import (
    Gate,
    message_event,
    response_event,
    tool_event,
    wait_until,
)
from project_kanban_core import models, rules
from project_kanban_core.errors import KanbanError


async def _project(store, key, members):
    async with store.txn() as board:
        return rules.create_project(
            board,
            models.ProjectCreate(name=key, key=key, members=members),
            time.time(),
            lambda _: True,
        )


async def _issue(
    store, project, assignee, status="todo", queued_at=None, title="t"
):
    async with store.txn() as board:
        issue = rules.create_issue(
            board,
            project["id"],
            models.IssueCreate(title=title, assignee=assignee, status=status),
            time.time(),
        )
        if queued_at is not None:
            issue["queued_at"] = queued_at
        return issue


def _status(store, issue):
    return next(i for i in store.read()["issues"] if i["id"] == issue["id"])


async def test_one_running_issue_per_agent_across_projects(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice", "bob"])
    rpt = await _project(store, "RPT", ["alice"])
    a1 = await _issue(store, web, "alice", queued_at=10)
    a2 = await _issue(store, rpt, "alice", queued_at=20)
    b1 = await _issue(store, web, "bob", queued_at=30)
    engine.capture(hub.ctx())

    assert await engine.run_pass() == 2  # alice and bob in parallel
    assert _status(store, a1)["status"] == "in_progress"
    assert _status(store, b1)["status"] == "in_progress"
    assert _status(store, a2)["status"] == "todo"
    assert await engine.run_pass() == 0  # alice still busy
    view = rules.dispatch_view(store.read())
    assert view["agents"]["alice"]["running"]["key"] == "WEB-1"
    assert [r["key"] for r in view["agents"]["alice"]["queue"]] == ["RPT-1"]
    assert rules.queue_position(store.read(), _status(store, a2)) == 1

    gate.release("alice")
    await wait_until(lambda: _status(store, a1)["status"] == "review")
    assert await engine.run_pass() == 1
    assert _status(store, a2)["status"] == "in_progress"
    gate.release("bob")
    await wait_until(lambda: _status(store, b1)["status"] == "review")
    await wait_until(lambda: _status(store, a2)["status"] == "review")


async def test_fifo_across_projects_by_queued_at(engine, hub, store):
    order = []
    hub.script = lambda agent, msg: order.append(
        msg.split("issue ")[1][:6]
    ) or [
        message_event("ok"),
    ]
    web = await _project(store, "WEB", ["alice"])
    rpt = await _project(store, "RPT", ["alice"])
    await _issue(store, web, "alice", queued_at=30)  # WEB-1
    await _issue(store, rpt, "alice", queued_at=10)  # RPT-1
    await _issue(store, web, "alice", queued_at=20)  # WEB-2
    engine.capture(hub.ctx())
    for _ in range(3):
        await engine.run_pass()
        await wait_until(lambda: not engine.runner.live_ids())
    assert [k.strip(":") for k in order] == ["RPT-1", "WEB-2", "WEB-1"]


async def test_archived_project_paused_then_resumed(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    async with store.txn() as board:
        rules.archive_project(board, web["id"], time.time())
    engine.capture(hub.ctx())
    assert await engine.run_pass() == 0
    assert rules.agent_queues(store.read()) == {}
    async with store.txn() as board:
        rules.unarchive_project(board, web["id"], time.time())
    assert _status(store, issue)["queued_at"] == issue["queued_at"]  # original
    assert await engine.run_pass() == 1


async def test_no_dispatch_before_base_ctx(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    await _issue(store, web, "alice")
    assert await engine.run_pass() == 0
    engine.capture(hub.ctx())
    assert await engine.run_pass() == 1


async def test_wake_up_latency(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    engine.sweep_interval = 60  # only the wake-up can start the run
    await engine.start()
    try:
        engine.capture(hub.ctx())
        issue = await _issue(store, web, "alice")
        started = time.monotonic()
        engine.wake()
        await wait_until(
            lambda: _status(store, issue)["status"] != "todo",
            timeout=1.0,
        )
        assert time.monotonic() - started < 1.0
        await wait_until(lambda: _status(store, issue)["status"] == "review")
        # Run finished → dispatcher woken → next queued issue starts.
        nxt = await _issue(store, web, "alice")
        engine.wake()
        await wait_until(lambda: _status(store, nxt)["status"] == "review")
    finally:
        await engine.stop()


async def test_orphan_recovery_runs_first(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    queued = await _issue(store, web, "alice", queued_at=time.time() - 50)
    orphan = await _issue(store, web, "alice", status="backlog")
    async with store.txn() as board:
        target = next(i for i in board["issues"] if i["id"] == orphan["id"])
        rules.start_run(target, time.time() - 100)  # crashed mid-run
    assert await engine.recover_orphans() == 1
    recovered = _status(store, orphan)
    assert recovered["status"] == "todo"
    assert recovered["last_run"]["outcome"] == "interrupted"
    queue = rules.agent_queues(store.read())["alice"]
    assert [i["id"] for i in queue] == [orphan["id"], queued["id"]]


async def test_run_on_busy_agent_queues(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    first = await _issue(store, web, "alice", status="backlog")
    second = await _issue(store, web, "alice", status="backlog")
    ctx = hub.ctx()
    result = await engine.run_issue(ctx, web["id"], first["id"], "zh")
    assert result["started"] is True and result["queue_position"] is None
    assert result["issue"]["key"] == "WEB-1"
    result = await engine.run_issue(ctx, web["id"], second["id"], None)
    assert result["started"] is False
    assert result["queue_position"] == 1
    assert result["issue"]["status"] == "todo"
    gate.release("alice")
    await wait_until(lambda: _status(store, first)["status"] == "review")
    # Not started automatically without a pass; a pass starts it.
    await engine.run_pass()
    await wait_until(lambda: _status(store, second)["status"] == "review")
    prompt = next(c[1] for c in hub.calls if c[1] != "/clear")
    assert prompt.startswith("你是项目「WEB」(WEB) 的成员智能体。")


async def test_failure_goes_to_backlog_without_retry(engine, hub, store):
    hub.script = lambda agent, msg: [RuntimeError("model exploded")]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "backlog")
    run = _status(store, issue)["last_run"]
    assert run["outcome"] == "failed" and run["error"] == "model exploded"
    assert await engine.run_pass() == 0  # no automatic retry
    result = await engine.runner.result(hub.ctx(), _status(store, issue))
    assert result == {"error": "model exploded"}


async def test_unknown_agent_fails_run(engine, hub, store):
    web = await _project(store, "WEB", ["dave"])  # not in FakeAgents
    issue = await _issue(store, web, "dave")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "backlog")
    assert _status(store, issue)["last_run"]["error"] == "Agent dave not found"


async def test_stop_running_issue(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    nxt = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    assert engine.runner.is_live(issue["id"])
    view = await engine.stop_issue(web["id"], issue["id"])
    assert view["status"] == "backlog"
    assert view["last_run"]["outcome"] == "stopped"
    await wait_until(lambda: not engine.runner.is_live(issue["id"]))
    assert _status(store, issue)["status"] == "backlog"  # task wrote nothing
    # Stopping again is a no-op returning the issue.
    assert (await engine.stop_issue(web["id"], issue["id"]))[
        "status"
    ] == "backlog"
    gate.release("alice")
    assert await engine.run_pass() == 1
    await wait_until(lambda: _status(store, nxt)["status"] == "review")
    with pytest.raises(KanbanError):
        await engine.stop_issue(web["id"], "missing")


async def test_previous_agent_session_deleted(engine, hub, store, tmp_path):
    class Session:
        def _get_save_path(self, session_id, user_id, channel):
            return str(tmp_path / f"{channel}-{user_id}-{session_id}.json")

    class Workspace:
        session = Session()

    hub.workspaces["bob"] = Workspace()
    web = await _project(store, "WEB", ["alice", "bob"])
    issue = await _issue(store, web, "alice", status="backlog")
    sid = rules.session_id_for(issue["id"])
    stale = tmp_path / f"console-default-{sid}.json"
    stale.write_text("{}")
    async with store.txn() as board:
        target = next(i for i in board["issues"] if i["id"] == issue["id"])
        target["last_run"] = {"agent_id": "bob", "session_id": sid}
    await engine.run_issue(hub.ctx(), web["id"], issue["id"], None)
    await wait_until(lambda: _status(store, issue)["status"] == "review")
    assert not stale.exists()  # R-RUN-2
    assert ("alice", "/clear", sid) in hub.calls


async def test_result_excerpt_and_trace(engine, hub, store):
    hub.script = lambda agent, msg: [
        tool_event("read_file"),
        tool_event("read_file", output=True),
        message_event("first"),
        message_event("final answer"),
    ]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "review")
    run = _status(store, issue)["last_run"]
    assert run["result_excerpt"] == "final answer"
    hub.history[("alice", run["session_id"])] = [
        {"role": "user", "content": []},
        {"role": "assistant", "type": "message", "content": []},
    ]
    result = await engine.runner.result(hub.ctx(), _status(store, issue))
    assert len(result["messages"]) == 1


async def test_dispatch_view_shapes(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    await _issue(store, web, "alice", title="Write copy")
    view = rules.dispatch_view(store.read())
    ref = view["agents"]["alice"]["queue"][0]
    assert ref == {
        "project_id": web["id"],
        "project_key": "WEB",
        "project_name": "WEB",
        "issue_id": ref["issue_id"],
        "key": "WEB-1",
        "title": "Write copy",
    }
    assert view["agents"]["alice"]["running"] is None


async def test_removed_member_not_in_queue(engine, hub, store):
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    async with store.txn() as board:
        project = rules.find_project(board, web["id"])
        project["members"] = []  # simulate a stale assignee
    assert rules.agent_queues(store.read()) == {}
    assert rules.queue_position(store.read(), _status(store, issue)) is None


async def test_shutdown_leaves_run_for_orphan_recovery(engine, hub, store):
    gate = Gate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    await engine.start(hub.ctx())
    await wait_until(lambda: engine.runner.is_live(issue["id"]))
    await engine.stop()
    await asyncio.sleep(0.05)
    assert _status(store, issue)["status"] == "in_progress"
    assert await engine.recover_orphans() == 1
    assert _status(store, issue)["status"] == "todo"


async def test_stop_racing_a_dispatch_pass_cancels_the_run(engine, hub, store):
    """Stop issued while a pass holds the lock still cancels the new run."""
    gate = Gate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    nxt = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    pass_task = asyncio.create_task(engine.run_pass())
    await asyncio.sleep(0)  # the pass is inside its transaction now
    view = await engine.stop_issue(web["id"], issue["id"])
    await pass_task
    assert view["status"] == "backlog"
    assert view["last_run"]["outcome"] == "stopped"
    await wait_until(lambda: not engine.runner.agent_running("alice"))
    assert not engine.runner.is_live(issue["id"])
    assert _status(store, nxt)["status"] == "todo"  # nothing runs twice


async def test_agent_busy_until_cancelled_run_unwinds(engine, hub, store):
    unwound = asyncio.Event()

    async def slow_gate(agent):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.2)  # host cleanup after cancel
            unwound.set()

    hub.gate = slow_gate
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    nxt = await _issue(store, web, "alice")
    engine.sweep_interval = 60
    await engine.start(hub.ctx())
    try:
        await wait_until(lambda: engine.runner.is_live(issue["id"]))
        hub.gate = None
        await engine.stop_issue(web["id"], issue["id"])
        await asyncio.sleep(0.05)
        assert engine.runner.agent_running("alice")  # still unwinding
        assert _status(store, nxt)["status"] == "todo"
        await asyncio.wait_for(unwound.wait(), 2)
        # The unwound task wakes the dispatcher: next issue starts on its own.
        await wait_until(lambda: _status(store, nxt)["status"] == "review")
    finally:
        await engine.stop()


async def test_outcome_write_is_retried(engine, hub, store, monkeypatch):
    monkeypatch.setattr("project_kanban_core.runner.OUTCOME_RETRY_DELAY", 0)
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    real_txn = store.txn
    calls = {"n": 0}

    def flaky_txn():
        calls["n"] += 1
        if calls["n"] == 2:  # 1st = dispatch flip, 2nd = first outcome try
            raise OSError("disk hiccup")
        return real_txn()

    monkeypatch.setattr(store, "txn", flaky_txn)
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "review")
    assert calls["n"] == 3


class DrainingGate:
    """Holds runs open and models a host stream that drains slowly after a
    cancel; records how many streams each agent has open at once."""

    def __init__(self, drain: float = 0.2) -> None:
        self.drain = drain
        self.open = asyncio.Event()
        self.active: dict = {}
        self.peak: dict = {}

    async def __call__(self, agent):
        self.active[agent] = self.active.get(agent, 0) + 1
        self.peak[agent] = max(self.peak.get(agent, 0), self.active[agent])
        try:
            await self.open.wait()
        except asyncio.CancelledError:
            await asyncio.sleep(self.drain)  # host still unwinding
            raise
        finally:
            self.active[agent] -= 1


async def test_run_while_stopped_run_still_unwinding_is_queued(
    engine, hub, store
):
    """P0: /run (and a pass) must not start a second stream for an agent
    whose stopped run is still draining, although the board says idle."""
    gate = DrainingGate()
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    first = await _issue(store, web, "alice", status="backlog")
    second = await _issue(store, web, "alice", status="backlog")
    ctx = hub.ctx()
    engine.sweep_interval = 60
    await engine.start(ctx)
    try:
        assert (await engine.run_issue(ctx, web["id"], first["id"], None))[
            "started"
        ]
        await wait_until(lambda: gate.active.get("alice") == 1)
        await engine.stop_issue(web["id"], first["id"])
        assert gate.active["alice"] == 1  # still draining
        assert rules.running_issue_of(store.read(), "alice") is None

        rerun_other = await engine.run_issue(
            ctx, web["id"], second["id"], None
        )
        rerun_same = await engine.run_issue(ctx, web["id"], first["id"], None)
        assert rerun_other["started"] is False
        assert rerun_other["queue_position"] == 1
        assert rerun_same["started"] is False
        assert rerun_same["queue_position"] == 2
        assert await engine.run_pass() == 0

        gate.open.set()  # later runs complete immediately
        # Drained → dispatcher woken → the queue runs one at a time.
        await wait_until(lambda: _status(store, second)["status"] == "review")
        await wait_until(lambda: _status(store, first)["status"] == "review")
        assert gate.peak["alice"] == 1
    finally:
        await engine.stop()


@pytest.mark.parametrize("first", ["pass", "run"])
async def test_interleaved_pass_and_run_start_one_run(
    engine, hub, store, first
):
    """P0: a pass and /run racing on the lock start exactly one run."""
    gate = DrainingGate(drain=0)
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    queued = await _issue(store, web, "alice")  # todo: the pass's head
    manual = await _issue(store, web, "alice", status="backlog")
    ctx = hub.ctx()
    engine.capture(ctx)
    calls = [
        engine.run_pass(),
        engine.run_issue(ctx, web["id"], manual["id"], None),
    ]
    if first == "run":
        calls.reverse()
    results = await asyncio.gather(*calls)
    run_result = results[1] if first == "pass" else results[0]
    live = engine.runner.live_ids()
    assert len(live) == 1
    statuses = {
        _status(store, queued)["status"],
        _status(store, manual)["status"],
    }
    assert statuses == {"in_progress", "todo"}
    assert run_result["started"] is (manual["id"] in live)
    await wait_until(lambda: gate.active.get("alice") == 1)
    assert gate.peak["alice"] == 1


async def test_pass_and_run_during_unwinding_start_nothing(engine, hub, store):
    gate = DrainingGate(drain=0.3)
    hub.gate = gate
    web = await _project(store, "WEB", ["alice"])
    running = await _issue(store, web, "alice")
    other = await _issue(store, web, "alice", status="backlog")
    ctx = hub.ctx()
    engine.capture(ctx)
    await engine.run_pass()
    await wait_until(lambda: gate.active.get("alice") == 1)
    await engine.stop_issue(web["id"], running["id"])
    launched, result = await asyncio.gather(
        engine.run_pass(),
        engine.run_issue(ctx, web["id"], other["id"], None),
    )
    assert launched == 0 and result["started"] is False
    await wait_until(lambda: not engine.runner.agent_running("alice"))
    assert gate.peak["alice"] == 1


async def test_failed_terminal_envelope_is_a_failed_run(engine, hub, store):
    """P0: a third-party harness reports errors as a normal last stream item
    (status=failed), not as an exception; the run must not succeed."""
    hub.script = lambda agent, msg: [
        message_event("partial answer"),
        response_event("failed", "codex exited with code 1"),
        message_event("must not be read"),
    ]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "backlog")
    run = _status(store, issue)["last_run"]
    assert run["outcome"] == "failed"
    assert run["error"] == "codex exited with code 1"
    assert run["result_excerpt"] is None
    prompts = [y for y in hub.yielded if y[1] != "/clear"]
    assert len(prompts) == 2  # stopped right at the failed envelope
    assert ("alice", prompts[0][1]) in hub.closed  # stream closed early
    result = await engine.runner.result(hub.ctx(), _status(store, issue))
    assert result == {"error": "codex exited with code 1"}


async def test_cancelled_terminal_envelope_is_a_failed_run(engine, hub, store):
    hub.script = lambda agent, msg: [response_event("cancelled")]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "backlog")
    run = _status(store, issue)["last_run"]
    assert run["outcome"] == "failed"
    assert run["error"] == "Run cancelled by the host"


async def test_failed_clear_stops_the_run_before_the_prompt(
    engine, hub, store
):
    hub.clear_script = lambda agent: [response_event("failed", "no model")]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "backlog")
    assert _status(store, issue)["last_run"]["error"] == "no model"
    assert [c[1] for c in hub.calls] == ["/clear"]  # prompt never sent


async def test_completed_envelopes_still_succeed(engine, hub, store):
    hub.clear_script = lambda agent: [response_event("completed")]
    hub.script = lambda agent, msg: [
        response_event("created"),
        response_event("in_progress"),
        message_event("all good"),
        response_event("completed"),
    ]
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    engine.capture(hub.ctx())
    await engine.run_pass()
    await wait_until(lambda: _status(store, issue)["status"] == "review")
    assert _status(store, issue)["last_run"]["result_excerpt"] == "all good"


async def test_orphan_recovery_runs_first_when_started_after_others_queued(
    engine,
    hub,
    store,
):
    """The orphan started after another issue was queued (its agent was
    busy until then): recovery must still put it ahead of that issue."""
    web = await _project(store, "WEB", ["alice"])
    queued = await _issue(store, web, "alice", queued_at=time.time() - 50)
    orphan = await _issue(store, web, "alice", status="backlog")
    async with store.txn() as board:
        target = next(i for i in board["issues"] if i["id"] == orphan["id"])
        rules.start_run(target, time.time() - 10)  # crashed mid-run
    assert await engine.recover_orphans() == 1
    queue = rules.agent_queues(store.read())["alice"]
    assert [i["id"] for i in queue] == [orphan["id"], queued["id"]]


INTERRUPTED = "I noticed that you have interrupted me."


class SwallowingGate:
    """Holds runs open and models the real host on a cancel: agentscope
    catches the ``CancelledError``, ends the reply with an interruption
    message and the chat stream finishes normally instead of raising."""

    def __init__(self, drain: float = 0.0) -> None:
        self.drain = drain
        self.swallowed: list = []

    async def __call__(self, agent):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(self.drain)  # host still unwinding
            self.swallowed.append(agent)

    def script(self, agent, msg):
        if agent in self.swallowed:
            return [message_event(INTERRUPTED)]
        return [message_event(f"done by {agent}")]


async def test_shutdown_when_host_swallows_cancel_writes_no_outcome(
    engine,
    hub,
    store,
):
    """A shutdown leaves the run ``in_progress`` for orphan recovery even
    though the host ends the cancelled stream normally."""
    gate = SwallowingGate()
    hub.gate = gate
    hub.script = gate.script
    web = await _project(store, "WEB", ["alice"])
    issue = await _issue(store, web, "alice")
    await engine.start(hub.ctx())
    await wait_until(lambda: engine.runner.is_live(issue["id"]))
    await engine.stop()
    await wait_until(lambda: not engine.runner.agent_running("alice"))
    assert gate.swallowed == ["alice"]
    current = _status(store, issue)
    assert current["status"] == "in_progress"
    assert current["last_run"]["outcome"] is None
    assert await engine.recover_orphans() == 1


async def test_stopped_run_unwinding_leaves_next_run_stream_alone(
    engine,
    hub,
    store,
):
    """A stopped run that is still finishing must neither publish into nor
    close the stream of the issue's next run (reassigned to bob)."""
    gate = SwallowingGate(drain=0.2)
    hub.gate = gate
    hub.script = gate.script
    web = await _project(store, "WEB", ["alice", "bob"])
    issue = await _issue(store, web, "alice", status="backlog")
    ctx = hub.ctx()
    engine.sweep_interval = 60
    await engine.start(ctx)
    try:
        assert (await engine.run_issue(ctx, web["id"], issue["id"], None))[
            "started"
        ]
        await engine.stop_issue(web["id"], issue["id"])
        async with store.txn() as board:
            rules.patch_issue(
                board,
                web["id"],
                issue["id"],
                models.IssuePatch(assignee="bob"),
                time.time(),
            )
        assert (await engine.run_issue(ctx, web["id"], issue["id"], None))[
            "started"
        ]
        _, channel = engine.runner.subscribe(issue["id"])
        assert channel is not None

        await wait_until(lambda: not engine.runner.agent_running("alice"))
        assert gate.swallowed == ["alice"]
        assert engine.runner.is_live(issue["id"])
        assert not channel.is_closed
        assert engine.runner.trace(issue["id"]) == []
        current = _status(store, issue)
        assert current["status"] == "in_progress"
        assert current["last_run"]["agent_id"] == "bob"
    finally:
        await engine.stop()
        await wait_until(lambda: not engine.runner.agent_running("bob"))


async def test_stop_racing_a_wake_up_does_not_hang(engine):
    """stop()'s cancel landing as a wake-up fires must end the loop
    (``asyncio.wait_for`` swallows that cancel on Python 3.11)."""
    engine.sweep_interval = 60
    await engine.start()
    await asyncio.sleep(0.01)  # the loop is waiting for a wake-up again
    loop = asyncio.get_running_loop()
    started = loop.time()
    # stop() swallows the CancelledError of this timeout, so a hang shows
    # up as the elapsed time rather than as a TimeoutError.
    async with asyncio.timeout(1):
        engine.wake()
        await engine.stop()  # cancels in the same step as the wake-up
    assert loop.time() - started < 0.5
