# -*- coding: utf-8 -*-
"""Storage: transactions, atomic write, reload, unreadable file, schema."""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from project_kanban_core.errors import KanbanError
from project_kanban_core.store import Store


async def test_missing_file_is_empty_board(store):
    board = store.read()
    assert board == {"schema_version": 1, "projects": [], "issues": []}
    assert not store.path.exists()  # reads never write


async def test_txn_writes_through_and_reads_back(store):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["projects"] == [{"id": "p1"}]
    assert store.read()["projects"] == [{"id": "p1"}]
    # A fresh store (another process) sees the same data.
    assert Store(store.data_dir).read()["projects"] == [{"id": "p1"}]


async def test_unchanged_txn_does_not_write(store):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    stat_before = os.stat(store.path)
    async with store.txn():
        pass
    stat_after = os.stat(store.path)
    assert stat_before.st_ino == stat_after.st_ino
    assert stat_before.st_mtime_ns == stat_after.st_mtime_ns


async def test_exception_rolls_back(store):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    with pytest.raises(RuntimeError):
        async with store.txn() as board:
            board["projects"].append({"id": "p2"})
            raise RuntimeError("boom")
    assert [p["id"] for p in store.read()["projects"]] == ["p1"]
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert [p["id"] for p in on_disk["projects"]] == ["p1"]


async def test_atomic_write_failure_keeps_old_file(store, monkeypatch):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    original = store.path.read_text(encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        async with store.txn() as board:
            board["projects"].append({"id": "p2"})
    monkeypatch.undo()
    assert store.path.read_text(encoding="utf-8") == original
    leftovers = [
        p.name for p in store.data_dir.iterdir() if p.suffix == ".tmp"
    ]
    assert leftovers == []
    assert [p["id"] for p in store.read()["projects"]] == ["p1"]


async def test_reload_on_external_change(store):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    assert len(store.read()["projects"]) == 1
    other = Store(store.data_dir)  # e.g. a second backend process
    async with other.txn() as board:
        board["projects"].append({"id": "p2"})
    assert [p["id"] for p in store.read()["projects"]] == ["p1", "p2"]
    # The next transaction also starts from the reloaded state.
    async with store.txn() as board:
        board["projects"].append({"id": "p3"})
    assert [p["id"] for p in other.read()["projects"]] == ["p1", "p2", "p3"]


async def test_unreadable_file_is_not_overwritten(store, monkeypatch):
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    store.data_dir.mkdir(parents=True)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KanbanError) as exc:
        store.read()
    assert exc.value.code == "BOARD_UNAVAILABLE"
    assert exc.value.status_code == 503
    with pytest.raises(KanbanError):
        async with store.txn() as board:
            board["projects"].append({"id": "p1"})
    assert store.path.read_text(encoding="utf-8") == "{not json"


async def test_unreadable_file_is_retried(store, monkeypatch):
    store.data_dir.mkdir(parents=True)
    store.path.write_text("{bad", encoding="utf-8")
    good = json.dumps({"schema_version": 1, "projects": [], "issues": []})

    def fix_file(_delay):
        store.path.write_text(good, encoding="utf-8")

    monkeypatch.setattr("project_kanban_core.store.time.sleep", fix_file)
    assert store.read()["projects"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "projects": [], "issues": []},
        {"projects": [], "issues": []},
        {"schema_version": 1, "projects": {}, "issues": []},
        [1, 2],
    ],
)
async def test_schema_guard(store, monkeypatch, payload):
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    store.data_dir.mkdir(parents=True)
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(KanbanError) as exc:
        store.read()
    assert exc.value.code == "BOARD_UNAVAILABLE"
    assert json.loads(store.path.read_text(encoding="utf-8")) == payload


async def test_concurrent_transactions_do_not_lose_updates(store):
    async def bump():
        async with store.txn() as board:
            board.setdefault("counter", 0)
            await asyncio.sleep(0)
            board["counter"] += 1

    await asyncio.gather(*(bump() for _ in range(20)))
    assert store.read()["counter"] == 20


def test_cross_process_lock(tmp_path: Path):
    """Two processes doing read-modify-write keep every increment."""
    backend = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        f"""
        import asyncio, sys
        sys.path.insert(0, {str(backend)!r})
        from project_kanban_core.store import Store

        async def main():
            store = Store({str(tmp_path / 'data')!r})
            for _ in range(25):
                async with store.txn() as board:
                    board["counter"] = board.get("counter", 0) + 1

        asyncio.run(main())
        """,
    )
    env = dict(os.environ)
    procs = [
        subprocess.Popen([sys.executable, "-c", script], env=env)
        for _ in range(2)
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    assert Store(tmp_path / "data").read()["counter"] == 50


async def test_os_error_on_read_is_retried_then_503(store, monkeypatch):
    """P1: PermissionError & co. map to BOARD_UNAVAILABLE, never a 500."""
    import builtins

    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    original = store.path.read_text(encoding="utf-8")
    fresh = Store(store.data_dir)  # nothing cached yet
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    real_open = builtins.open
    attempts = []

    def denied(path, *args, **kwargs):
        if str(path) == str(fresh.path):
            attempts.append(path)
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(
        "project_kanban_core.store.open", denied, raising=False
    )
    with pytest.raises(KanbanError) as exc:
        fresh.read()
    assert exc.value.code == "BOARD_UNAVAILABLE"
    assert exc.value.status_code == 503
    assert "PermissionError" in exc.value.message
    assert len(attempts) == 5  # retried like an unparseable file
    with pytest.raises(KanbanError) as exc:
        async with fresh.txn() as board:
            board["projects"].append({"id": "p2"})
    assert exc.value.code == "BOARD_UNAVAILABLE"
    monkeypatch.undo()
    assert store.path.read_text(encoding="utf-8") == original  # untouched
    assert [p["id"] for p in fresh.read()["projects"]] == ["p1"]


async def test_os_error_recovers_after_transient_failure(store, monkeypatch):
    import builtins

    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    fresh = Store(store.data_dir)
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    real_open = builtins.open
    failures = {"left": 2}

    def flaky(path, *args, **kwargs):
        if str(path) == str(fresh.path) and failures["left"]:
            failures["left"] -= 1
            raise OSError(5, "Input/output error")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("project_kanban_core.store.open", flaky, raising=False)
    assert [p["id"] for p in fresh.read()["projects"]] == ["p1"]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)
async def test_unreadable_data_dir_is_503(store):
    async with store.txn() as board:
        board["projects"].append({"id": "p1"})
    fresh = Store(store.data_dir)
    store.data_dir.chmod(0o000)
    try:
        with pytest.raises(KanbanError) as exc:
            fresh.read()  # os.stat fails with PermissionError
        assert exc.value.code == "BOARD_UNAVAILABLE"
        with pytest.raises(KanbanError) as exc:
            async with fresh.txn():
                pass  # the lock file cannot be opened either
        assert exc.value.code == "BOARD_UNAVAILABLE"
    finally:
        store.data_dir.chmod(0o755)
    assert [p["id"] for p in fresh.read()["projects"]] == ["p1"]
