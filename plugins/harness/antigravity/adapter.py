# -*- coding: utf-8 -*-
"""Antigravity sessions over one long-lived ``agy acp`` server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from qwenpaw.plugins import SDKError
from qwenpaw.plugins.harnesses import (
    HarnessApprovalSummary,
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

from .acp import PROMPT_DONE, AcpClient, AcpError, redact
from .discovery import Discovery, discover
from .event_mapper import EventMapper, history_items
from .io_helpers import finish, run_io
from .models import DEFAULT_MODEL_ID, catalog, default_model, model_ids

logger = logging.getLogger(__name__)

_SESSIONS_FILE = "antigravity_sessions.json"
_CATALOG_FILE = "antigravity_catalog.json"
_STORE_VERSION = 1
_APPROVAL_TIMEOUT_SECONDS = 300
_CANCEL_GRACE_SECONDS = 10
_MODELS_TTL_SECONDS = 600
_LOGIN_HINT = (
    "Antigravity CLI requires login. Run 'agy' interactively as the same "
    "OS user running QwenPaw."
)
# permission_mode -> ACP session mode; full-access also auto-approves.
_MODES = {
    "configured": "default",
    "accept-edits": "accept-edits",
    "plan": "plan",
    "full-access": "default",
}
_ALLOW = ("allow_once", "allow_always")
_REJECT = ("reject_once", "reject_always")


class AntigravityBackend:
    """Map QwenPaw sessions onto ACP sessions of one shared agy server.

    One process serves every session of this backend: ACP multiplexes
    sessions by id and all per-session choices (cwd, model, thinking level,
    mode) are protocol calls, so nothing forces a process per identity.
    Auto-approval is answered per request instead of with a process flag.
    """

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
        self._session_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._models_lock = asyncio.Lock()
        self._client: AcpClient | None = None
        self._active = 0
        # identity -> ACP session id loaded in the current process
        self._live: dict[str, str] = {}
        # session id -> mode and model selection already applied
        self._applied: dict[str, dict[str, Any]] = {}
        self._contexts: dict[str, HarnessCallContext] = {}
        self._approval_tasks: dict[str, set[asyncio.Task]] = {}
        self._idle: dict[str, asyncio.Task] = {}
        self._models: tuple[HarnessModel, ...] = ()
        self._models_expires = 0.0
        self._discovery = Discovery()
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

    async def status(self, context: HarnessCallContext) -> HarnessStatus:
        """Inspect installation, ACP support and login; no model request."""
        del context
        await self.prepare()
        status = await self._discovery.status(self._binary, self._env)
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
        """Models and thinking levels as an ACP session reports them."""
        del context
        await self.prepare()
        async with self._models_lock:
            if time.monotonic() < self._models_expires:
                return self._models
            self._active += 1
            try:
                self._models = catalog(await self._catalog_session())
            except Exception as exc:  # pylint: disable=broad-except
                reason = _LOGIN_HINT if _auth_error(exc) else str(exc)
                self._models = (default_model(description=redact(reason)),)
            finally:
                self._active -= 1
            self._models_expires = time.monotonic() + (
                _MODELS_TTL_SECONDS if len(self._models) > 1 else 5
            )
            await self._stop_if_idle()
            return self._models

    async def _catalog_session(self):
        """Open the one reusable catalog session, so listing models never
        leaves a new empty conversation behind; close it afterwards."""
        client = await self._ensure_client()
        cwd = str(self._context.state_dir)
        saved = await self._context.storage.aread_json(_CATALOG_FILE, {})
        session_id = (saved or {}).get("session_id")
        result = None
        if isinstance(session_id, str) and session_id:
            try:
                result = await client.request(
                    "session/resume",
                    {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
                )
            except AcpError as exc:
                if exc.auth_required:
                    raise
        if result is None:
            result = await client.request(
                "session/new",
                {"cwd": cwd, "mcpServers": []},
            )
            session_id = result["sessionId"]
            await self._context.storage.awrite_json(
                _CATALOG_FILE,
                {"session_id": session_id},
            )
        try:
            await client.request("session/close", {"sessionId": session_id})
        except AcpError:
            logger.debug("Antigravity catalog session close failed")
        return result

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
        """Replay the mapped conversation through ``session/load``."""
        record = await self._claim_session(context)
        if not record:
            return ()
        identity = self._session_key(context)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            self._active += 1
            try:
                return await finish(self._load(identity, record, context))
            except AcpError as exc:
                logger.warning("Antigravity history replay failed: %s", exc)
                return ()
            finally:
                self._active -= 1

    async def _load(self, identity, record, context):
        self._cancel_idle(identity)
        client = await self._ensure_client()
        session_id = record["conversation_id"]
        queue = client.subscribe(session_id)
        try:
            result = await client.request(
                "session/load",
                {
                    "sessionId": session_id,
                    "cwd": record["cwd"],
                    "mcpServers": [],
                },
            )
        finally:
            client.unsubscribe(session_id)
        updates = []
        while not queue.empty():
            update = queue.get_nowait()
            if isinstance(update, dict):
                updates.append(update)
        self._track(identity, session_id, result)
        idle = (context.settings or {}).get("idle_timeout_seconds", 600)
        self._arm_idle(identity, idle if type(idle) is int else 600)
        return history_items(updates)

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

    async def _ensure_client(self):
        async with self._client_lock:
            if self._closed:
                raise SDKError("Antigravity backend is closed")
            if self._client is not None and not self._client.closed:
                return self._client
            if self._client is not None:
                await finish(self._client.close())
            # Sessions loaded into a dead process must be resumed again.
            self._client = None
            self._live.clear()
            self._applied.clear()
            status = await run_io(discover, self._binary, self._env)
            if not status.available:
                raise RuntimeError(status.error)
            launch = asyncio.ensure_future(
                AcpClient.start(
                    [status.runtime_path, "acp"],
                    self._context.state_dir,
                    self._env,
                    self._on_request,
                ),
            )
            try:
                self._client = await finish(launch)
            except asyncio.CancelledError:
                if (
                    launch.done()
                    and not launch.cancelled()
                    and launch.exception() is None
                ):
                    await finish(launch.result().close())
                raise
            return self._client

    async def _stop_if_idle(self):
        """Stop the server once no session is loaded and no call runs."""
        async with self._client_lock:
            client = self._client
            if client is not None and not self._live and not self._active:
                self._client = None
                await finish(client.close())

    def _track(self, identity, session_id, result):
        result = result if isinstance(result, dict) else {}
        modes = result.get("modes") or {}
        self._live[identity] = session_id
        self._applied[session_id] = {
            "mode": modes.get("currentModeId"),
            "selection": None,
            "model_ids": {key for key, _ in model_ids(result)},
        }

    async def _open_session(self, client, identity, record, cwd):
        session_id = self._live.get(identity)
        if session_id:
            return session_id
        params = {"cwd": str(cwd), "mcpServers": []}
        if record:
            session_id = record["conversation_id"]
            try:
                result = await client.request(
                    "session/resume",
                    {"sessionId": session_id, **params},
                )
            except AcpError as exc:
                if exc.auth_required:
                    raise
                raise RuntimeError(
                    f"Could not resume Antigravity conversation "
                    f"{session_id}: {exc}. Clear the session to start over.",
                ) from exc
        else:
            result = await client.request("session/new", params)
            session_id = (result or {}).get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("Antigravity session/new returned no id")
            # The ACP session id is the agy conversation id.
            await self._store_record(
                identity,
                {"conversation_id": session_id, "cwd": str(cwd)},
            )
        self._track(identity, session_id, result)
        return session_id

    async def _configure(self, client, session_id, options):
        """Apply mode, model and thinking level only when they change."""
        applied = self._applied[session_id]
        mode = _MODES[options["permission_mode"]]
        if applied["mode"] != mode:
            await client.request(
                "session/set_mode",
                {"sessionId": session_id, "modeId": mode},
            )
            applied["mode"] = mode
        model = options["model"] or DEFAULT_MODEL_ID
        # Listed ids already name their thinking level; only the CLI
        # default model takes a separate one.
        effort = options["effort"] if model == DEFAULT_MODEL_ID else None
        selection = (model, effort)
        if applied["selection"] == selection:
            return
        if model != DEFAULT_MODEL_ID:
            if model not in applied["model_ids"]:
                raise ValueError(
                    f"Antigravity does not offer model {model!r}; "
                    "choose one from the model list",
                )
            await client.request(
                "session/set_model",
                {"sessionId": session_id, "modelId": model},
            )
        if effort:
            await client.request(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "thought_level",
                    "value": effort,
                },
            )
        applied["selection"] = selection

    async def reset_session(self, context: HarnessCallContext) -> None:
        """Close the ACP session and forget the mapping, never agy data."""
        await self._claim_session(context)
        identity = self._session_key(context)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            await finish(self._reset(identity))

    async def _reset(self, identity):
        await self._close_session(identity)
        await self._store_record(identity, None)
        await self._stop_if_idle()

    async def _close_session(self, identity):
        self._cancel_idle(identity)
        session_id = self._live.pop(identity, None)
        if session_id is None:
            return
        self._applied.pop(session_id, None)
        client = self._client
        if client is None or client.closed:
            return
        try:
            async with asyncio.timeout(_CANCEL_GRACE_SECONDS):
                await client.request(
                    "session/close",
                    {"sessionId": session_id},
                )
        except (AcpError, TimeoutError):
            logger.debug("Antigravity session/close failed")

    def _cancel_idle(self, identity):
        idle = self._idle.pop(identity, None)
        if idle is not None and idle is not asyncio.current_task():
            idle.cancel()

    def _arm_idle(self, identity, delay):
        self._cancel_idle(identity)
        if not self._closed and identity in self._live:
            self._idle[identity] = asyncio.create_task(
                self._expire(identity, delay),
            )

    async def _expire(self, identity, delay):
        await asyncio.sleep(delay)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            if self._idle.get(identity) is asyncio.current_task():
                del self._idle[identity]
            await finish(self._close_session(identity))
            await finish(self._stop_if_idle())

    async def close(self) -> None:
        """Stop the server and timers while preserving durable mappings."""
        async with self._close_lock:
            self._closed = True
            await finish(self._close())

    async def _close(self):
        tasks = [
            task
            for group in (
                self._idle.values(),
                *self._approval_tasks.values(),
            )
            for task in group
            if task is not asyncio.current_task()
        ]
        self._idle.clear()
        self._approval_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._client_lock:
            client, self._client = self._client, None
            self._live.clear()
            self._applied.clear()
            if client is not None:
                await client.close()
        self._turn_locks.clear()

    async def _on_request(self, method, params):
        if method != "session/request_permission":
            raise AcpError(f"Method not found: {method}", -32601)
        return await self._permission(params)

    async def _permission(self, params):
        """Route an agy permission request to the running turn's approver."""
        options = params.get("options") or []
        session_id = params.get("sessionId")
        identity = next(
            (key for key, value in self._live.items() if value == session_id),
            None,
        )
        context = self._contexts.get(identity) if identity else None
        if context is None:
            # Between turns no live QwenPaw call can route the request.
            return _outcome(options, _REJECT)
        if context.settings.get("permission_mode") == "full-access":
            return _outcome(options, _ALLOW)
        tool = params.get("toolCall") or {}
        name = str(tool.get("name") or tool.get("kind") or "tool")
        title = str(tool.get("title") or name)
        summary = HarnessApprovalSummary(
            tool_name=name,
            title=title,
            description=title,
            severity="high" if tool.get("kind") == "execute" else "medium",
            parameters=json.loads(
                json.dumps(
                    {
                        "provider": "antigravity",
                        "provider_item_id": tool.get("toolCallId"),
                        "tool_name": name,
                        "kind": tool.get("kind"),
                        "input": tool.get("rawInput") or tool.get("args"),
                    },
                    default=str,
                ),
            ),
        )
        task = asyncio.create_task(
            context.request_approval(
                summary,
                timeout=_APPROVAL_TIMEOUT_SECONDS,
            ),
        )
        group = self._approval_tasks.setdefault(identity, set())
        group.add(task)
        try:
            decision = await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # The turn ended or was cancelled; ACP expects "cancelled".
            return {"outcome": {"outcome": "cancelled"}}
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Antigravity approval failed; rejecting tool call",
                exc_info=True,
            )
            decision = "rejected"
        finally:
            group.discard(task)
        if decision == "approved":
            return _outcome(options, _ALLOW)
        if decision == "cancelled":
            return {"outcome": {"outcome": "cancelled"}}
        return _outcome(options, _REJECT)

    # One turn validates, opens, configures, streams and cleans up.
    # pylint: disable-next=too-many-branches,too-many-statements
    async def run_turn(
        self,
        context: HarnessCallContext,
        request: HarnessRunRequest,
    ) -> AsyncIterator[HarnessEvent]:
        """Stream one ``session/prompt``; failures keep the mapping."""
        identity = self._session_key(context)
        self._active += 1
        try:
            async with self._turn_locks.setdefault(identity, asyncio.Lock()):
                idle_seconds = 600
                client = session_id = prompt = None
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
                    cwd = await run_io(Path(context.cwd).resolve)
                    record = await self._claim_session(context)
                    if record and record["cwd"] != str(cwd):
                        raise ValueError(
                            "Working directory changed; clear the session "
                            "before continuing.",
                        )
                    self._cancel_idle(identity)
                    client = await self._ensure_client()
                    session_id = await self._open_session(
                        client,
                        identity,
                        record,
                        cwd,
                    )
                    await self._configure(client, session_id, options)
                    mapper = EventMapper(context.session_id, uuid.uuid4().hex)
                    queue = client.subscribe(session_id)
                    self._contexts[identity] = context
                    prompt = asyncio.create_task(
                        client.request(
                            "session/prompt",
                            {
                                "sessionId": session_id,
                                "prompt": [
                                    {"type": "text", "text": request.prompt},
                                ],
                            },
                        ),
                    )
                    prompt.add_done_callback(
                        lambda _: queue.put_nowait(PROMPT_DONE),
                    )
                    async with asyncio.timeout(
                        options["print_timeout_seconds"],
                    ):
                        while True:
                            update = await queue.get()
                            if update is PROMPT_DONE or update is None:
                                break
                            for event in mapper.map(update):
                                yield event
                        # Shielded: on timeout the prompt must stay pending
                        # so the cleanup below can send session/cancel.
                        result = await asyncio.shield(prompt)
                    for event in mapper.finish(result):
                        yield event
                except Exception as exc:
                    if isinstance(exc, TimeoutError) and prompt is not None:
                        text = "Antigravity turn timed out"
                    elif _auth_error(exc):
                        text = _LOGIN_HINT
                    else:
                        text = redact(str(exc)) or type(exc).__name__
                    yield HarnessEvent(HarnessEventKind.ERROR, text=text)
                finally:
                    self._contexts.pop(identity, None)
                    if client is not None and session_id is not None:
                        client.unsubscribe(session_id)
                        await finish(
                            self._end_turn(
                                client,
                                identity,
                                session_id,
                                prompt,
                            ),
                        )
                        self._arm_idle(identity, idle_seconds)
        finally:
            self._active -= 1

    async def _end_turn(self, client, identity, session_id, prompt):
        """Answer pending approvals, then cancel an unfinished prompt.

        ACP keeps the session usable after ``session/cancel`` once the prompt
        resolves with ``cancelled``; a session that does not settle in time
        is closed and resumed on the next turn.
        """
        tasks = self._approval_tasks.pop(identity, set())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if prompt is None or prompt.done():
            return
        try:
            await client.notify("session/cancel", {"sessionId": session_id})
            async with asyncio.timeout(_CANCEL_GRACE_SECONDS):
                await prompt
            return
        except TimeoutError:
            pass
        except Exception:  # pylint: disable=broad-except
            # The prompt failed or the server is gone: nothing to settle.
            return
        logger.warning("Antigravity prompt did not settle after cancel")
        await self._close_session(identity)


def _outcome(options, kinds):
    """Select the first offered option of the preferred ACP kinds."""
    for kind in kinds:
        for option in options:
            if isinstance(option, dict) and option.get("kind") == kind:
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option.get("optionId"),
                    },
                }
    return {"outcome": {"outcome": "cancelled"}}


def _auth_error(exc):
    return isinstance(exc, AcpError) and exc.auth_required


def validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Reject unsafe or ambiguous values before talking to agy."""
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
    if options["permission_mode"] not in _MODES:
        raise ValueError(
            "permission_mode must be one of: " + ", ".join(_MODES),
        )
    options["effort"] = settings.get(
        "reasoning_effort",
        settings.get("effort"),
    )
    if options["effort"] is not None and not isinstance(
        options["effort"],
        str,
    ):
        raise ValueError("reasoning effort must be a string")
    options["model"] = settings.get("model")
    if options["model"] is not None and not isinstance(options["model"], str):
        raise ValueError("model must be a string")
    return options
