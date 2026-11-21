# -*- coding: utf-8 -*-
"""Native Linux Antigravity sessions over persistent NDJSON processes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from qwenpaw.plugins import SDKError
from qwenpaw.plugins.harnesses import (
    HarnessCallContext,
    HarnessContext,
    HarnessDiscoveredMCPServer,
    HarnessDiscoveredSkill,
    HarnessEvent,
    HarnessEventKind,
    HarnessHistoryItem,
    HarnessLogin,
    HarnessModel,
    HarnessOperationNotSupportedError,
    HarnessRunRequest,
    HarnessStatus,
)

from .discovery import Discovery
from .event_mapper import EventMapper
from .io_helpers import finish, run_io
from .models import DEFAULT_MODEL_ID, ModelDiscovery
from .process import StreamProcess, redact

_SESSIONS_FILE = "antigravity_sessions.json"
_STORE_VERSION = 1


class AntigravityBackend:
    """Keep one serialized agy transport per durable conversation."""

    def __init__(
        self,
        context: HarnessContext,
        settings: dict[str, Any] | None = None,
        *,
        binary: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._context = context
        self._binary = (
            binary or str((settings or {}).get("binary") or "").strip() or None
        )
        self._env = {**os.environ, **(env or {})}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, StreamProcess] = {}
        self._idle: dict[str, asyncio.Task] = {}
        self._session_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._discovery = Discovery()
        self._model_discovery = ModelDiscovery()
        self._prepared = False
        self._closed = False

    async def prepare(self) -> None:
        """Validate target state, copying the original legacy file once."""
        async with self._session_lock:
            if self._closed:
                raise SDKError("Antigravity backend is closed")
            if self._prepared:
                return
            try:
                missing = object()
                payload = await self._context.storage.aread_json(
                    _SESSIONS_FILE,
                    missing,
                )
                if payload is missing:
                    legacy = (
                        self._context.workspace_dir
                        / "harnesses"
                        / _SESSIONS_FILE
                    )
                    try:
                        raw = await run_io(legacy.read_bytes)
                    except FileNotFoundError:
                        payload = {"version": _STORE_VERSION, "sessions": {}}
                    else:
                        payload = json.loads(raw)
                        self._validate_store(payload)
                        await self._context.storage.awrite_bytes(
                            _SESSIONS_FILE,
                            raw,
                        )
                self._validate_store(payload)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise SDKError(
                    "Could not load Antigravity session state; restore "
                    f"{_SESSIONS_FILE} or fix file permissions: {exc}",
                ) from exc
            self._sessions = dict(payload["sessions"])
            self._prepared = True

    @staticmethod
    def _validate_store(payload):
        sessions = (
            payload.get("sessions")
            if isinstance(payload, dict)
            and payload.get("version") == _STORE_VERSION
            else None
        )
        if not isinstance(sessions, dict) or not all(
            isinstance(key, str)
            and key
            and isinstance(record, dict)
            and isinstance(record.get("conversation_id"), str)
            and record["conversation_id"]
            and isinstance(record.get("cwd"), str)
            for key, record in sessions.items()
        ):
            raise SDKError(
                "Antigravity session mapping is invalid; "
                "restore its saved JSON state",
            )

    @staticmethod
    def _session_key(context):
        return json.dumps(
            [context.user_id, context.channel, context.session_id],
        )

    async def _save_sessions(self, sessions):
        async def commit():
            try:
                await self._context.storage.awrite_json(
                    _SESSIONS_FILE,
                    {"version": _STORE_VERSION, "sessions": sessions},
                )
            except (OSError, UnicodeError) as exc:
                raise SDKError(
                    "Could not save Antigravity session state; "
                    f"check storage permissions: {exc}",
                ) from exc
            self._sessions = sessions

        await finish(commit())

    async def _claim_session(self, context):
        """Move a legacy session-only key to the caller's identity once."""
        await self.prepare()
        identity = self._session_key(context)
        async with self._session_lock:
            if (
                identity not in self._sessions
                and context.session_id in self._sessions
            ):
                sessions = dict(self._sessions)
                sessions[identity] = sessions.pop(context.session_id)
                await self._save_sessions(sessions)
            return self._sessions.get(identity)

    async def _store_record(self, identity, record):
        async with self._session_lock:
            sessions = dict(self._sessions)
            if record is None:
                sessions.pop(identity, None)
            else:
                sessions[identity] = record
            await self._save_sessions(sessions)

    async def _status(self):
        return await self._discovery.status(self._binary, self._env)

    async def status(self, context: HarnessCallContext) -> HarnessStatus:
        """Inspect installation and compatibility with no model request."""
        del context
        await self.prepare()
        status = await self._status()
        account = None
        if status.account is not None:
            # HarnessStatus has no tri-state; keep the honest one visible.
            account = {
                **status.account,
                "authentication_status": status.authentication_status,
            }
        return HarnessStatus(
            installed=status.installed,
            authenticated=status.authenticated,
            account=account,
            runtime_path=status.runtime_path,
            runtime_source=status.runtime_source,
            error=status.error,
            available=status.available,
        )

    async def start_login(
        self,
        context: HarnessCallContext,
        *,
        device_code: bool = False,
    ) -> HarnessLogin:
        """Require the provider's interactive same-user login flow."""
        del context, device_code
        raise HarnessOperationNotSupportedError(
            "Run 'agy' manually as the same OS user running QwenPaw.",
        )

    async def logout(self, context: HarnessCallContext) -> None:
        """Leave provider-owned credentials under manual management."""
        del context
        raise HarnessOperationNotSupportedError(
            "Manage Antigravity authentication manually as the same OS user.",
        )

    async def models(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessModel, ...]:
        """Return cached provider models plus a configured-default choice."""
        del context
        status = await self._status()
        return await self._model_discovery.models(
            status.runtime_path,
            self._env,
            (status.account or {}).get("cli_version"),
        )

    async def discover_skills(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessDiscoveredSkill, ...]:
        del context
        return ()

    async def discover_mcp(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessDiscoveredMCPServer, ...]:
        del context
        return ()

    async def history(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessHistoryItem, ...]:
        del context
        return ()

    async def run_command(
        self,
        context: HarnessCallContext,
        command: str,
        arguments: str,
    ) -> tuple[HarnessEvent, ...]:
        del context, arguments
        raise HarnessOperationNotSupportedError(
            f"Unsupported Antigravity command: /{command}",
        )

    async def _close_process(self, identity):
        idle = self._idle.pop(identity, None)
        if idle is not None and idle is not asyncio.current_task():
            idle.cancel()
        process = self._processes.pop(identity, None)
        if process is not None:
            await process.close()

    async def _start_process(self, identity, argv, cwd):
        launch = asyncio.ensure_future(
            StreamProcess.start(argv, cwd, self._env),
        )
        try:
            process = await finish(launch)
        except asyncio.CancelledError:
            if (
                launch.done()
                and not launch.cancelled()
                and launch.exception() is None
            ):
                await finish(launch.result().close())
            raise
        self._processes[identity] = process
        return process

    async def reset_session(self, context: HarnessCallContext) -> None:
        """Reap transport and forget the local mapping, never provider data."""
        await self._claim_session(context)
        identity = self._session_key(context)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            await finish(self._reset(identity))

    async def _reset(self, identity):
        await self._close_process(identity)
        await self._store_record(identity, None)

    async def _expire(self, identity, delay):
        await asyncio.sleep(delay)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            await finish(self._close_process(identity))

    async def close(self) -> None:
        """Reap every active transport while preserving durable mappings."""
        async with self._close_lock:
            self._closed = True
            await finish(self._close())

    async def _close(self):
        idle = [
            task
            for task in self._idle.values()
            if task is not asyncio.current_task()
        ]
        self._idle.clear()
        for task in idle:
            task.cancel()
        await asyncio.gather(*idle, return_exceptions=True)
        results = await asyncio.gather(
            *(self._close_process(key) for key in tuple(self._processes)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self._turn_locks.clear()

    # One turn validates, (re)starts, streams and persists in a single lease.
    # pylint: disable-next=too-many-branches,too-many-statements
    async def run_turn(
        self,
        context: HarnessCallContext,
        request: HarnessRunRequest,
    ) -> AsyncIterator[HarnessEvent]:
        """Stream one turn; failures retain the explicit resume mapping."""
        identity = self._session_key(context)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            healthy = False
            idle_seconds = 600
            try:
                if request.attachments:
                    raise ValueError(
                        "Antigravity does not support attachments",
                    )
                if request.prompt.lstrip().startswith("/"):
                    raise ValueError(
                        "Antigravity slash commands are unsupported",
                    )
                options = validate_settings(context.settings)
                idle_seconds = options["idle_timeout_seconds"]
                fingerprint = hashlib.sha256(
                    json.dumps(options, sort_keys=True).encode(),
                ).hexdigest()
                cwd = await run_io(Path(context.cwd).resolve)
                record = await self._claim_session(context)
                if record and record["cwd"] != str(cwd):
                    raise ValueError(
                        "Working directory changed; clear the session "
                        "before continuing.",
                    )
                idle = self._idle.pop(identity, None)
                if idle:
                    idle.cancel()
                process = self._processes.get(identity)
                if process and (
                    process.process.returncode is not None
                    or (record or {}).get("settings_fingerprint")
                    != fingerprint
                ):
                    await self._close_process(identity)
                    process = None
                if not process:
                    status = await self._status()
                    if not status.available:
                        raise RuntimeError(status.error)
                    process = await self._start_process(
                        identity,
                        build_argv(status.runtime_path, options, record),
                        cwd,
                    )
                mapper = EventMapper(context.session_id, uuid.uuid4().hex)
                process.stderr.clear()
                async with asyncio.timeout(
                    options["print_timeout_seconds"] + 10,
                ):
                    await process.send(request.prompt)
                    while True:
                        raw = await process.read()
                        name = raw.get("event")
                        payload = raw.get(name, raw)
                        if not isinstance(payload, dict):
                            raise RuntimeError(
                                "Antigravity returned a malformed event",
                            )
                        cid = payload.get("conversation_id")
                        if cid and record and cid != record["conversation_id"]:
                            raise RuntimeError(
                                "Antigravity conversation ID changed; "
                                "clear the session.",
                            )
                        if name == "init":
                            cid = raw.get("conversation_id")
                            if not isinstance(cid, str) or not cid:
                                raise RuntimeError(
                                    "Antigravity init missing conversation ID",
                                )
                            if record and cid != record["conversation_id"]:
                                raise RuntimeError(
                                    "Antigravity resumed a different "
                                    "conversation",
                                )
                            record = {
                                **(record or {}),
                                "conversation_id": cid,
                                "cwd": str(cwd),
                                "settings_fingerprint": fingerprint,
                            }
                            await self._store_record(identity, record)
                        if name == "result" and record is None:
                            raise RuntimeError(
                                "Antigravity result before conversation "
                                "initialization",
                            )
                        events = mapper.map(raw)
                        if name == "result":
                            usage = payload.get("usage")
                            delta = usage_delta(
                                record.get("last_usage"),
                                usage,
                            )
                            if isinstance(usage, dict):
                                record = {**record, "last_usage": usage}
                                await self._store_record(identity, record)
                            events = [
                                event
                                if event.kind == HarnessEventKind.TEXT_DELTA
                                else replace(
                                    event,
                                    data={
                                        **event.data,
                                        "usage_delta": delta,
                                        "usage_delta_scope": (
                                            "conversation_cumulative_"
                                            "difference"
                                            if delta is not None
                                            else "unknown"
                                        ),
                                    },
                                )
                                for event in events
                            ]
                            # Give the continuously running stderr reader a
                            # scheduling opportunity before capturing its tail.
                            await asyncio.sleep(0)
                            diagnostics = redact(
                                process.stderr.decode(errors="replace"),
                            ).strip()
                            if diagnostics:
                                item_id = mapper.prefix + ":diagnostics"
                                yield HarnessEvent(
                                    HarnessEventKind.TOOL_STARTED,
                                    item_id=item_id,
                                    tool_name="Antigravity diagnostics",
                                )
                                yield HarnessEvent(
                                    HarnessEventKind.TOOL_COMPLETED,
                                    item_id=item_id,
                                    tool_name="Antigravity diagnostics",
                                    text=diagnostics,
                                )
                        for event in events:
                            yield event
                        if name == "result":
                            healthy = payload.get("status") == "SUCCESS"
                            return
            except Exception as exc:
                yield HarnessEvent(
                    HarnessEventKind.ERROR,
                    text=redact(str(exc)) or "Antigravity turn timed out",
                )
            finally:
                if healthy and not self._closed:
                    self._idle[identity] = asyncio.create_task(
                        self._expire(identity, idle_seconds),
                    )
                else:
                    await finish(self._close_process(identity))


def build_argv(
    binary: str,
    options: dict[str, Any],
    record: dict | None,
) -> list[str]:
    """Translate validated settings into native flags, never shell text."""
    argv = [
        binary,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--print-timeout",
        str(options["print_timeout_seconds"]) + "s",
    ]
    if record:
        argv += ["--conversation", record["conversation_id"]]
    for key in ("model", "effort"):
        if options.get(key) and options[key] != DEFAULT_MODEL_ID:
            argv += ["--" + key, options[key]]
    if options["permission_mode"] == "full-access":
        argv.append("--dangerously-skip-permissions")
    return argv


def validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Reject unsafe or ambiguous values before launching a process."""
    options = {}
    for key, default, low, high in (
        ("print_timeout_seconds", 3600, 1, 3600),
        ("idle_timeout_seconds", 600, 30, 86400),
    ):
        value = settings.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(
                f"{key} must be an integer from {low} to {high}",
            )
        options[key] = value
    options["permission_mode"] = settings.get("permission_mode", "configured")
    if options["permission_mode"] not in {"configured", "full-access"}:
        raise ValueError("permission_mode must be configured or full-access")
    options["effort"] = settings.get(
        "reasoning_effort",
        settings.get("effort"),
    )
    if options["effort"] not in {None, "", "low", "medium", "high", "max"}:
        raise ValueError("effort must be low, medium, high, or max")
    options["model"] = settings.get("model")
    if options["model"] is not None and not isinstance(options["model"], str):
        raise ValueError("model must be a string")
    if options["model"] not in {None, "", DEFAULT_MODEL_ID}:
        # Listed model ids already name their effort variant and agy
        # rejects a separate --effort for them, so only the CLI default
        # model takes one.
        options["effort"] = None
    return options


def usage_delta(previous: Any, current: Any) -> dict[str, int] | None:
    """Subtract only a persisted monotonic conversation-scoped baseline."""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    if not current or previous.keys() != current.keys():
        return None
    if any(
        type(current[key]) is not int
        or type(previous[key]) is not int
        or previous[key] < 0
        or current[key] < previous[key]
        for key in current
    ):
        return None
    return {key: current[key] - previous[key] for key in current}
