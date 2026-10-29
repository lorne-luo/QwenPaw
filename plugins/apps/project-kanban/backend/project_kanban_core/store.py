# -*- coding: utf-8 -*-
"""``board.json`` persistence (requirements §7).

- One file holds ``{schema_version, projects, issues}``.
- Every read-modify-write runs in :meth:`Store.txn`: asyncio lock plus an
  exclusive ``fcntl.flock`` on ``.board.lock`` (cross-process), reload when
  the file changed, atomic write-through before the lock is released.
- Reads use an in-memory cache refreshed by the same change check.
- An unreadable file or a newer ``schema_version`` raises
  ``BOARD_UNAVAILABLE``; the file is never overwritten in that case.

Objects returned by :meth:`Store.read` and committed by :meth:`Store.txn`
are the cache itself: callers must treat them as read-only and build new
dicts for responses.
"""

import asyncio
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Tuple

try:  # POSIX advisory locking; Windows falls back to single-process.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from . import get_logger
from .errors import KanbanError
from .models import SCHEMA_VERSION

logger = get_logger(__name__)

_READ_RETRIES = 5
_READ_RETRY_DELAY = 0.05

Signature = Optional[Tuple[int, int, int]]


def empty_board() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "projects": [], "issues": []}


def _dump(board: Dict[str, Any]) -> str:
    return json.dumps(board, ensure_ascii=False, indent=1)


def _lock_file(path: Path) -> Any:
    fh = open(path, "a+", encoding="utf-8")  # pylint: disable=R1732
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except BaseException:
        fh.close()
        raise
    return fh


def _unlock_file(fh: Any) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


class Store:
    """Cached, transactional access to ``<data_dir>/board.json``."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "board.json"
        self.lock_path = self.data_dir / ".board.lock"
        self._lock = asyncio.Lock()
        self._board: Optional[Dict[str, Any]] = None
        self._raw: str = ""
        self._sig: Signature = None
        self._last_error = ""

    # ── Loading ──────────────────────────────────────────────────────
    def _signature(self) -> Signature:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:  # e.g. permission denied on data/
            raise self._unavailable(
                f"board.json not accessible: {exc}"
            ) from exc
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _unavailable(self, message: str) -> KanbanError:
        if message != self._last_error:
            logger.error("[project-kanban] board unavailable: %s", message)
            self._last_error = message
        return KanbanError("BOARD_UNAVAILABLE", message)

    def _read_file(self) -> Tuple[Signature, Optional[bytes]]:
        try:
            with open(self.path, "rb") as fh:
                st = os.fstat(fh.fileno())
                data = fh.read()
        except FileNotFoundError:
            return None, None
        return (st.st_mtime_ns, st.st_size, st.st_ino), data

    def _parse(self, data: bytes) -> Dict[str, Any]:
        board = json.loads(data.decode("utf-8"))
        if not isinstance(board, dict):
            raise ValueError("top level is not an object")
        version = board.get("schema_version")
        if not isinstance(version, int) or version < 1:
            raise ValueError("missing schema_version")
        if version > SCHEMA_VERSION:
            raise LookupError(
                f"schema_version {version} is newer than supported "
                f"{SCHEMA_VERSION}",
            )
        if not isinstance(board.get("projects"), list) or not isinstance(
            board.get("issues"),
            list,
        ):
            raise ValueError("projects/issues must be lists")
        return board

    def _load(self) -> None:
        last_err = ""
        for attempt in range(_READ_RETRIES):
            try:
                sig, data = self._read_file()
            except OSError as exc:  # permission denied, I/O error, ...
                last_err = f"{exc.__class__.__name__}: {exc}"
                if attempt + 1 < _READ_RETRIES:
                    time.sleep(_READ_RETRY_DELAY)
                continue
            if sig is None:
                board = empty_board()
                self._set_cache(board, _dump(board), None)
                return
            try:
                board = self._parse(data or b"")
            except LookupError as exc:  # newer schema: do not retry
                raise self._unavailable(str(exc)) from exc
            except (ValueError, UnicodeDecodeError) as exc:
                last_err = str(exc) or exc.__class__.__name__
                if attempt + 1 < _READ_RETRIES:
                    time.sleep(_READ_RETRY_DELAY)
                continue
            self._set_cache(board, _dump(board), sig)
            return
        raise self._unavailable(f"board.json unreadable: {last_err}")

    def _set_cache(
        self,
        board: Dict[str, Any],
        raw: str,
        sig: Signature,
    ) -> None:
        self._board = board
        self._raw = raw
        self._sig = sig
        self._last_error = ""

    def _refresh(self) -> None:
        if self._board is not None and self._signature() == self._sig:
            return
        self._load()

    def read(self) -> Dict[str, Any]:
        """Return the current board (read-only; refreshed if changed)."""
        self._refresh()
        assert self._board is not None
        return self._board

    # ── Writing ──────────────────────────────────────────────────────
    def _write(self, raw: str) -> Signature:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.data_dir),
            prefix=".board.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return self._signature()

    async def _acquire_file_lock(self) -> Any:
        if fcntl is None:  # pragma: no cover
            return None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._unavailable(f"data dir not accessible: {exc}") from exc
        task = asyncio.ensure_future(
            asyncio.to_thread(_lock_file, self.lock_path),
        )
        try:
            return await asyncio.shield(task)
        except OSError as exc:
            raise self._unavailable(
                f"lock file not accessible: {exc}"
            ) from exc
        except asyncio.CancelledError:
            # The thread may still obtain the lock; release it when it does.
            def _release_late(done: "asyncio.Future") -> None:
                if not done.cancelled() and done.exception() is None:
                    _unlock_file(done.result())

            task.add_done_callback(_release_late)
            raise

    @asynccontextmanager
    async def txn(self) -> AsyncIterator[Dict[str, Any]]:
        """Serialize a read-modify-write across coroutines and processes.

        Yields a private copy of the board. On normal exit the copy is
        written atomically (only if it changed) before the locks are
        released; on an exception nothing is written. Waiting for the
        ``flock`` runs in a thread; the write itself is synchronous.
        """
        async with self._lock:
            fh = await self._acquire_file_lock()
            try:
                self._refresh()
                board = json.loads(self._raw)
                yield board
                raw = _dump(board)
                if raw != self._raw:
                    # Synchronous on purpose: a cancellation cannot land
                    # between replacing the file, updating the cache and
                    # releasing the locks.
                    sig = self._write(raw)
                    self._set_cache(board, raw, sig)
            finally:
                if fh is not None:
                    _unlock_file(fh)  # LOCK_UN never blocks
