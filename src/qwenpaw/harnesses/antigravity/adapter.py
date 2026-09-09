# -*- coding: utf-8 -*-
"""Native Linux Antigravity sessions over persistent NDJSON processes."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from ..base import HarnessAdapter, HarnessOperationNotSupportedError
from ..events import HarnessAttachment, HarnessEvent, HarnessEventKind
from ..events import HarnessModel, HarnessProvider
from .discovery import Discovery
from .event_mapper import EventMapper
from .models import ModelDiscovery
from .process import StreamProcess, redact
from .session_store import SessionStore


class AntigravityAdapter(HarnessAdapter):
    """Keep one serialized provider transport per durable local session."""

    def __init__(
        self,
        state_dir: Path,
        binary: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._binary = binary
        self._env = {**os.environ, **(env or {})}
        self._store = SessionStore(state_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, StreamProcess] = {}
        self._idle: dict[str, asyncio.Task] = {}
        self._model_discovery = ModelDiscovery()
        self._discovery = Discovery()

    @property
    def capability_unavailable_message(self) -> str | None:
        """Explain why dynamic discovery is unavailable when CLI is missing."""
        from .discovery import discover

        status = discover(self._binary, self._env)
        if not status.available:
            return status.error
        return None

    @property
    def models_fallback_message(self) -> str | None:
        """Explain why fallback model discovery is used."""
        return self._model_discovery.reason

    async def status(self) -> HarnessProvider:
        """Inspect installation and compatibility with no model request."""
        return await self._discovery.status(self._binary, self._env)

    async def start_login(
        self, device_code: bool = False,
    ) -> dict[str, Any]:
        """Require the provider's interactive same-user login flow."""
        raise HarnessOperationNotSupportedError(
            "Run 'agy' manually as the same OS user running QwenPaw.",
        )

    async def logout(self) -> None:
        """Leave provider-owned credentials under manual management."""
        raise HarnessOperationNotSupportedError(
            "Manage Antigravity authentication manually as the same OS user.",
        )

    async def models(self) -> list[HarnessModel]:
        """Return cached provider models plus a configured-default choice."""
        status = await self.status()
        return await self._model_discovery.models(
            status.runtime_path, self._env,
            (status.account or {}).get("cli_version"),
        )

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _close(self, session_id: str) -> None:
        idle = self._idle.pop(session_id, None)
        if idle and idle is not asyncio.current_task():
            idle.cancel()
        process = self._processes.pop(session_id, None)
        if process:
            await process.close()

    async def reset_session(self, session_id: str) -> None:
        """Reap transport and forget the local mapping, never provider data."""
        async with self._lock(session_id):
            await self._close(session_id)
            self._store.sessions.pop(session_id, None)
            self._store.save()

    async def _expire(self, session_id: str, delay: int) -> None:
        await asyncio.sleep(delay)
        async with self._lock(session_id):
            await self._close(session_id)

    async def stop(self) -> None:
        """Reap every active transport while preserving durable mappings."""
        for idle in list(self._idle.values()):
            idle.cancel()
        self._idle.clear()
        await asyncio.gather(*(
            self._close(key) for key in list(self._processes)
        ))

    async def run_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        cwd: Path,
        settings: dict[str, Any],
        attachments: list[HarnessAttachment] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        """Stream one turn; failures retain the explicit resume mapping."""
        async with self._lock(session_id):
            healthy = False
            idle_seconds = 600
            try:
                if attachments:
                    raise ValueError(
                        "Antigravity does not support attachments",
                    )
                if prompt.lstrip().startswith("/"):
                    raise ValueError(
                        "Antigravity slash commands are unsupported",
                    )
                options = validate_settings(settings)
                idle_seconds = options["idle_timeout_seconds"]
                fingerprint = hashlib.sha256(json.dumps(
                    options, sort_keys=True,
                ).encode()).hexdigest()
                cwd = Path(cwd).resolve()
                record = self._store.sessions.get(session_id)
                if record and record["cwd"] != str(cwd):
                    raise ValueError(
                        "Working directory changed; clear the session "
                        "before continuing.",
                    )
                idle = self._idle.pop(session_id, None)
                if idle:
                    idle.cancel()
                process = self._processes.get(session_id)
                if process and (
                    process.process.returncode is not None
                    or (record or {}).get("settings_fingerprint")
                    != fingerprint
                ):
                    await self._close(session_id)
                    process = None
                if not process:
                    status = await self.status()
                    if not status.available:
                        raise RuntimeError(status.error)
                    argv = build_argv(status.runtime_path, options, record)
                    process = await StreamProcess.start(argv, cwd, self._env)
                    self._processes[session_id] = process
                mapper = EventMapper(session_id, uuid.uuid4().hex)
                process.stderr.clear()
                async with asyncio.timeout(
                    options["print_timeout_seconds"] + 10,
                ):
                    await process.send(prompt)
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
                                **(record or {}), "conversation_id": cid,
                                "cwd": str(cwd),
                                "settings_fingerprint": fingerprint,
                            }
                            self._store.sessions[session_id] = record
                            self._store.save()
                        if name == "result" and record is None:
                            raise RuntimeError(
                                "Antigravity result before conversation "
                                "initialization",
                            )
                        events = mapper.map(raw)
                        if name == "result":
                            usage = payload.get("usage")
                            delta = usage_delta(record.get("last_usage"),
                                                usage)
                            if isinstance(usage, dict):
                                record["last_usage"] = usage
                                self._store.save()
                            for event in events:
                                if event.kind != HarnessEventKind.TEXT_DELTA:
                                    event.data["usage_delta"] = delta
                                    event.data["usage_delta_scope"] = (
                                        "conversation_cumulative_difference"
                                        if delta is not None else "unknown"
                                    )
                            # Give the continuously running stderr reader a
                            # scheduling opportunity before capturing its tail.
                            await asyncio.sleep(0)
                            diagnostics = redact(
                                process.stderr.decode(errors="replace"),
                            ).strip()
                            if diagnostics:
                                item_id = mapper.prefix + ":diagnostics"
                                yield HarnessEvent(
                                    kind=HarnessEventKind.TOOL_STARTED,
                                    item_id=item_id,
                                    tool_name="Antigravity diagnostics",
                                )
                                yield HarnessEvent(
                                    kind=HarnessEventKind.TOOL_COMPLETED,
                                    item_id=item_id,
                                    tool_name="Antigravity diagnostics",
                                    text=diagnostics,
                                )
                        for event in events:
                            yield event
                        if name == "result":
                            healthy = payload.get("status") == "SUCCESS"
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield HarnessEvent(
                    kind=HarnessEventKind.ERROR,
                    text=redact(str(exc)) or "Antigravity turn timed out",
                )
            finally:
                if healthy:
                    self._idle[session_id] = asyncio.create_task(
                        self._expire(session_id, idle_seconds),
                    )
                else:
                    await asyncio.shield(self._close(session_id))


def build_argv(
    binary: str, options: dict[str, Any], record: dict | None,
) -> list[str]:
    """Translate validated settings into native flags, never shell text."""
    argv = [
        binary, "--input-format", "stream-json", "--output-format",
        "stream-json", "--print-timeout",
        str(options["print_timeout_seconds"]) + "s",
    ]
    if record:
        argv += ["--conversation", record["conversation_id"]]
    for key in ("model", "effort"):
        if options.get(key) and options[key] != "default":
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
        "reasoning_effort", settings.get("effort"),
    )
    if options["effort"] not in {None, "", "low", "medium", "high", "max"}:
        raise ValueError("effort must be low, medium, high, or max")
    options["model"] = settings.get("model")
    if options["model"] is not None and not isinstance(options["model"], str):
        raise ValueError("model must be a string")
    if options["model"] not in {None, "", "default"}:
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
    if any(type(current[key]) is not int or type(previous[key]) is not int
           or previous[key] < 0 or current[key] < previous[key]
           for key in current):
        return None
    return {key: current[key] - previous[key] for key in current}
