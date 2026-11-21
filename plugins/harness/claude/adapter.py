# -*- coding: utf-8 -*-
"""Claude Code SDK backend using only the public QwenPaw plugin contract."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    ToolPermissionContext,
    get_session_messages,
)
from qwenpaw.plugins import SDKError
from qwenpaw.plugins.harnesses import (
    HarnessApprovalSummary,
    HarnessAttachment,
    HarnessAttachmentKind,
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
    HarnessRuntimeCapabilities,
    HarnessStatus,
)
from . import environment
from .discovery import resolve_claude_binary_info
from .event_mapper import ClaudeEventMapper, history_items
from .io_helpers import finish, run_io, stop_process
from .models import DEFAULT_ALIAS, catalog, from_server_info
from .projection import (
    cli_server_name,
    materialize_skill_plugin,
    mcp_servers,
    tool_policies,
)
from .session_reader import SessionReader

logger = logging.getLogger(__name__)

_SESSIONS_FILE = "claude_sessions.json"
_CONNECT_TIMEOUT_SECONDS = 30
_STATUS_TIMEOUT_SECONDS = 10
# Matches the host tool-guard default the in-core backend used.
_APPROVAL_TIMEOUT_SECONDS = 300
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
_LOGIN_MESSAGE = (
    "Claude Code sign-in is an interactive flow that must be completed on "
    "the host: run 'claude auth login' there, or add ANTHROPIC_AUTH_TOKEN "
    "or ANTHROPIC_API_KEY to this agent's environment."
)
_MISSING_CONVERSATION = "No conversation found with session ID"
_RESET_NOTICE = (
    "The Claude Code conversation this chat was linked to no longer exists "
    "on the host, so a fresh one was started. Earlier turns are not visible "
    "to the agent.\n\n"
)


class ClaudeBackend:
    """Own Claude Code SDK clients and mappings inside one workspace.

    ``discover_skills`` reports nothing. Claude Code's handshake carries no
    ``skills`` collection; skills arrive folded into ``commands`` alongside
    every built-in slash command, with no source or scope field to tell them
    apart, so the spec declares ``provider_skills_discovery=False``.

    Slash commands reach :meth:`run_turn` verbatim: the spec declares
    ``passthrough_commands=True`` because the CLI parses them itself, which
    also keeps a long command such as ``/init`` streaming while it works.
    """

    def __init__(
        self,
        context: HarnessContext,
        settings: dict[str, Any] | None = None,
        *,
        binary: str | None = None,
        client_factory: Callable[[ClaudeAgentOptions], Any] | None = None,
    ) -> None:
        self._context = context
        self._state_dir = context.state_dir
        self._binary = (
            binary or str((settings or {}).get("binary") or "").strip() or None
        )
        self._client_factory = client_factory or ClaudeSDKClient
        self._sessions: dict[str, str] = {}
        # Native IDs of fresh conversations, recorded once the CLI answers.
        self._pending: dict[str, str] = {}
        self._clients: dict[str, Any] = {}
        self._readers: dict[str, SessionReader] = {}
        self._client_keys: dict[str, tuple[Any, ...]] = {}
        self._owned_clients: dict[int, Any] = {}
        self._contexts: dict[str, HarnessCallContext] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._approval_tasks: dict[str, set[asyncio.Task]] = {}
        self._session_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._processes: set[Any] = set()
        self._cli_tasks: set[asyncio.Task] = set()
        self._prepared = False
        self._closed = False

    async def prepare(self) -> None:
        """Validate target state, copying the original legacy file once."""
        async with self._session_lock:
            if self._closed:
                raise SDKError("Claude Code backend is closed")
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
                        payload = {}
                    else:
                        payload = json.loads(raw)
                        self._validate_sessions(payload)
                        await self._context.storage.awrite_bytes(
                            _SESSIONS_FILE,
                            raw,
                        )
                self._validate_sessions(payload)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise SDKError(
                    "Could not load Claude Code session state; restore "
                    f"{_SESSIONS_FILE} or fix file permissions: {exc}",
                ) from exc
            self._sessions = dict(payload)
            self._prepared = True

    @staticmethod
    def _validate_sessions(payload):
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and key and value
            for key, value in payload.items()
        ):
            raise SDKError(
                "Claude Code session mapping is invalid; "
                "restore its saved JSON state",
            )

    @staticmethod
    def _session_key(context):
        return json.dumps(
            [context.user_id, context.channel, context.session_id],
        )

    async def _save_sessions(self, payload):
        async def commit():
            try:
                await self._context.storage.awrite_json(
                    _SESSIONS_FILE,
                    payload,
                )
            except (OSError, UnicodeError) as exc:
                raise SDKError(
                    "Could not save Claude Code session state; "
                    f"check storage permissions: {exc}",
                ) from exc
            self._sessions = payload

        await finish(commit())

    async def _claim_session(self, context):
        """Return the native ID, adopting a legacy session-ID-only key."""
        key = self._session_key(context)
        if key not in self._sessions and context.session_id in self._sessions:
            payload = dict(self._sessions)
            payload[key] = payload.pop(context.session_id)
            await self._save_sessions(payload)
        return self._sessions.get(key)

    async def _resolution(self):
        try:
            return await run_io(resolve_claude_binary_info, self._binary)
        except OSError as exc:
            raise SDKError(
                "Could not discover Claude Code CLI; "
                f"check its executable path: {exc}",
            ) from exc

    async def _require_resolution(self):
        resolution = await self._resolution()
        if resolution is None:
            raise SDKError(
                "Claude Code CLI executable was not found; repair the "
                "plugin or configure a valid executable.",
            )
        return resolution

    async def status(self, context: HarnessCallContext) -> HarnessStatus:
        await self.prepare()
        resolution = await self._resolution()
        if resolution is None:
            return HarnessStatus(
                installed=False,
                error="Claude Code CLI executable was not found.",
            )
        values = {
            "installed": True,
            "runtime_path": str(resolution.path),
            "runtime_source": resolution.source,
        }
        try:
            output = await self._run_cli(context, "auth", "status", "--json")
        except (SDKError, asyncio.TimeoutError) as exc:
            return HarnessStatus(
                **values,
                error=str(exc) or "Claude Code CLI status timed out.",
            )
        status_fields = _parse_status(output)
        authenticated = bool(status_fields.get("loggedIn"))
        account: dict[str, Any] | None = None
        if authenticated:
            account = {
                "type": str(status_fields.get("authMethod") or "claude"),
            }
            for source, target in (
                ("email", "email"),
                ("orgName", "organization"),
                ("subscriptionType", "plan"),
            ):
                value = status_fields.get(source)
                if value:
                    account[target] = str(value)
        return HarnessStatus(
            **values,
            authenticated=authenticated,
            account=account,
        )

    async def _environment(self, context):
        """Return the full environment the CLI effectively runs under."""
        return await run_io(
            environment.resolve,
            environment.agent_env(context.settings),
        )

    async def _spawn(self, resolution, *arguments, **kwargs):
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                str(resolution.path),
                *arguments,
                **kwargs,
            ),
        )
        try:
            try:
                process = await finish(launch)
            except OSError as exc:
                raise SDKError(
                    f"Could not launch Claude Code CLI: {exc}. Repair the "
                    "plugin or configure a valid executable.",
                ) from exc
        except asyncio.CancelledError:
            if (
                launch.done()
                and not launch.cancelled()
                and launch.exception() is None
            ):
                process = launch.result()
                self._processes.add(process)
                await finish(self._stop_process(process))
            raise
        self._processes.add(process)
        return process

    async def _stop_process(self, process):
        await stop_process(process, drain=True)
        self._processes.discard(process)

    async def _run_cli(self, context, *arguments, check=False):
        task = asyncio.current_task()
        self._cli_tasks.add(task)
        process = None
        try:
            process = await self._spawn(
                await self._require_resolution(),
                *arguments,
                env=await self._environment(context),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(_STATUS_TIMEOUT_SECONDS):
                stdout, _ = await process.communicate()
            output = stdout.decode(errors="replace")
            if check and process.returncode != 0:
                raise SDKError(
                    f"claude {' '.join(arguments)} failed "
                    f"(exit {process.returncode}): {output.strip()}",
                )
            return output
        finally:
            try:
                if process is not None:
                    await finish(self._stop_process(process))
            finally:
                self._cli_tasks.discard(task)

    async def start_login(
        self,
        context: HarnessCallContext,
        *,
        device_code: bool = False,
    ) -> HarnessLogin:
        """Explain why sign-in cannot be driven from the console.

        The CLI prints an OAuth URL and then blocks reading the authorization
        code from its own stdin, and the redirect delivers that code to the
        operator's browser rather than to this host.
        """
        del context, device_code
        raise HarnessOperationNotSupportedError(_LOGIN_MESSAGE)

    async def logout(self, context: HarnessCallContext) -> None:
        await self._run_cli(context, "auth", "logout", check=True)

    def _new_client(self, options):
        client = self._client_factory(options)
        self._owned_clients[id(client)] = client
        return client

    async def _disconnect(self, client):
        try:
            await client.disconnect()
        except (OSError, ClaudeSDKError) as exc:
            raise SDKError(
                f"Could not disconnect Claude Code SDK: {exc}",
            ) from exc
        self._owned_clients.pop(id(client), None)

    async def _connect(self, client):
        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                await client.connect()
        except (OSError, asyncio.TimeoutError, ClaudeSDKError) as exc:
            raise SDKError(
                "Could not connect Claude Code SDK; check CLI installation "
                f"and authentication: {exc}",
            ) from exc

    async def models(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessModel, ...]:
        """Return the models Claude Code offers this agent.

        The catalog comes from the CLI handshake, so it already reflects any
        gateway routing in the agent environment and carries each model's
        real effort support. The static alias catalog is the fallback for a
        CLI that cannot be reached.
        """
        env = environment.agent_env(context.settings)
        resolution = await self._resolution()
        info = None
        if resolution is not None:
            client = self._new_client(
                ClaudeAgentOptions(
                    cli_path=resolution.path,
                    env=dict(env),
                    setting_sources=[],
                ),
            )
            try:
                await self._connect(client)
                async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                    info = await client.get_server_info()
            except (OSError, SDKError, ClaudeSDKError, asyncio.TimeoutError):
                info = None
            finally:
                await finish(self._disconnect(client))
        models = from_server_info((info or {}).get("models"))
        return models or catalog(await self._environment(context))

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
        raise HarnessOperationNotSupportedError(
            "Claude Code SDK does not expose provider MCP discovery.",
        )

    async def history(
        self,
        context: HarnessCallContext,
    ) -> tuple[HarnessHistoryItem, ...]:
        """Read Claude Code's local JSONL transcript through the SDK."""
        await self.prepare()
        async with self._session_lock:
            native_id = await self._claim_session(context)
            if not native_id:
                return ()
            try:
                messages = await run_io(
                    get_session_messages,
                    native_id,
                    str(context.cwd),
                )
            except (OSError, ValueError) as exc:
                raise SDKError(
                    f"Could not read Claude Code session history: {exc}",
                ) from exc
        return tuple(
            item for message in messages for item in history_items(message)
        )

    def _client_key(self, context):
        settings = context.settings
        capabilities = (
            context.runtime_capabilities or HarnessRuntimeCapabilities()
        )
        return (
            str(context.cwd),
            str(settings.get("model") or ""),
            str(settings.get("reasoning_effort") or ""),
            str(settings.get("permission_mode") or "default"),
            environment.fingerprint(environment.agent_env(settings)),
            capabilities.fingerprint,
        )

    def _reusable_client(self, identity, key):
        """Return the session's client if its settings match and it is live."""
        client = self._clients.get(identity)
        reader = self._readers.get(identity)
        if (
            client is None
            or reader is None
            or reader.closed
            or self._client_keys.get(identity) != key
        ):
            return None
        return client

    async def _evict(self, identity):
        """Retire one cached client and its reader in one operation."""
        client = self._clients.pop(identity, None)
        reader = self._readers.pop(identity, None)
        self._client_keys.pop(identity, None)
        try:
            if client is not None:
                await self._disconnect(client)
        finally:
            if reader is not None:
                await reader.close()

    async def _client_for_session(self, context):
        """Return a connected client, and whether its thread was restarted."""
        await self.prepare()
        identity = self._session_key(context)
        key = self._client_key(context)
        capabilities = (
            context.runtime_capabilities or HarnessRuntimeCapabilities()
        )
        async with self._session_lock:
            existing = self._reusable_client(identity, key)
            if existing is not None:
                return existing, False
            await finish(self._evict(identity))
            resumable = await self._claim_session(context)
            is_resume = bool(resumable)
            native_id = (
                resumable or self._pending.get(identity) or str(uuid.uuid4())
            )
            try:
                plugin_path = await run_io(
                    materialize_skill_plugin,
                    self._state_dir,
                    capabilities,
                )
            except OSError as exc:
                raise SDKError(
                    f"Could not project Claude Code runtime Skills: {exc}",
                ) from exc
            cli_path = (await self._require_resolution()).path
            was_reset = False
            try:
                client = await self._open_client(
                    context,
                    native_id,
                    is_resume,
                    plugin_path,
                    cli_path,
                )
            except SDKError as exc:
                cause = exc.__cause__
                if not (
                    is_resume
                    and isinstance(cause, ProcessError)
                    and _MISSING_CONVERSATION in str(cause)
                ):
                    raise
                payload = dict(self._sessions)
                payload.pop(identity, None)
                await self._save_sessions(payload)
                native_id = str(uuid.uuid4())
                was_reset = True
                client = await self._open_client(
                    context,
                    native_id,
                    False,
                    plugin_path,
                    cli_path,
                )
            if not is_resume or was_reset:
                self._pending[identity] = native_id
            self._clients[identity] = client
            self._readers[identity] = SessionReader(client)
            self._client_keys[identity] = key
            return client, was_reset

    async def _open_client(
        self,
        context,
        native_id,
        resume,
        plugin_path,
        cli_path,
    ):
        options = self._options(
            context,
            native_id,
            resume,
            plugin_path,
            cli_path,
        )
        client = self._new_client(options)
        try:
            await self._connect(client)
        except BaseException:
            await finish(self._disconnect(client))
            raise
        return client

    def _options(
        self,
        context,
        native_id,
        resume,
        plugin_path,
        cli_path,
    ) -> ClaudeAgentOptions:
        settings = context.settings
        identity = self._session_key(context)
        permission_mode = str(settings.get("permission_mode") or "default")
        effort = settings.get("reasoning_effort")
        capabilities = (
            context.runtime_capabilities or HarnessRuntimeCapabilities()
        )
        allowed_tools, disallowed_tools = tool_policies(capabilities)
        # The echoed prompt tells SessionReader whether the CLI folded a
        # prompt into a turn that was already under way.
        extra_args: dict[str, str | None] = {"replay-user-messages": None}
        if effort in _EFFORT_LEVELS:
            extra_args["effort"] = str(effort)
        if permission_mode == "bypassPermissions":
            extra_args["dangerously-skip-permissions"] = None
        model = str(settings.get("model") or "").strip()
        return ClaudeAgentOptions(
            cli_path=cli_path,
            cwd=context.cwd,
            env=environment.agent_env(settings),
            resume=native_id if resume else None,
            session_id=None if resume else native_id,
            model=None if model in ("", DEFAULT_ALIAS) else model,
            extra_args=extra_args,
            include_partial_messages=True,
            permission_mode=permission_mode,
            can_use_tool=lambda tool_name, input_data, permission: (
                self._approve_tool(
                    identity,
                    tool_name,
                    input_data,
                    permission,
                )
            ),
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[
                            lambda data, tool_use_id, hook_context: (
                                self._gate_mcp_tool(
                                    identity,
                                    capabilities,
                                    data,
                                    tool_use_id,
                                )
                            ),
                        ],
                        timeout=_APPROVAL_TIMEOUT_SECONDS + 30,
                    ),
                ],
            },
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            mcp_servers=mcp_servers(capabilities),
            strict_mcp_config=True,
            setting_sources=["user", "project", "local"],
            plugins=(
                [{"type": "local", "path": str(plugin_path)}]
                if plugin_path is not None
                else []
            ),
            skills="all",
        )

    async def run_turn(
        self,
        context: HarnessCallContext,
        request: HarnessRunRequest,
    ) -> AsyncIterator[HarnessEvent]:
        """Send one turn to a persistent Claude Code SDK client."""
        identity = self._session_key(context)
        lock = self._turn_locks.setdefault(identity, asyncio.Lock())
        async with lock:
            self._contexts[identity] = context
            client = None
            try:
                client, was_reset = await self._client_for_session(context)
                mapper = ClaudeEventMapper()
                if was_reset:
                    yield HarnessEvent(
                        HarnessEventKind.TEXT_DELTA,
                        text=_RESET_NOTICE,
                    )
                query_input = (
                    self._attachment_input(
                        request.prompt,
                        context.cwd,
                        request.attachments,
                    )
                    if request.attachments
                    else request.prompt
                )
                messages = self._readers[identity].start_turn()
                await client.query(query_input)
                async for message in messages:
                    await self._commit_session(identity)
                    for event in mapper.convert(message):
                        yield event
            except (asyncio.CancelledError, GeneratorExit):
                if client is not None:
                    await finish(self._abandon_client(identity, client))
                raise
            finally:
                self._contexts.pop(identity, None)
                tasks = self._approval_tasks.pop(identity, set())
                for task in tasks:
                    task.cancel()
                if tasks:
                    await finish(
                        asyncio.gather(*tasks, return_exceptions=True),
                    )

    async def _abandon_client(self, identity, client):
        """Retire an interrupted client without masking the cancellation.

        Interrupt leaves a terminal result in the SDK receive queue, so the
        client is evicted before anything is awaited and no later turn can
        consume that result. Cleanup is best effort.
        """
        reader = None
        if self._clients.get(identity) is client:
            self._clients.pop(identity, None)
            reader = self._readers.pop(identity, None)
            self._client_keys.pop(identity, None)
            self._pending.pop(identity, None)
        operations = [client.interrupt, lambda: self._disconnect(client)]
        if reader is not None:
            operations.append(reader.close)
        for operation in operations:
            try:
                async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                    await operation()
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Claude Code cancellation cleanup failed",
                    exc_info=True,
                )

    async def _commit_session(self, identity):
        """Persist a fresh native ID once the CLI has answered.

        Recording it at connect time is too early: Claude Code only writes
        the transcript this ID names once a turn produces output, so a
        process that dies mid-first-turn would leave a mapping that every
        later ``--resume`` rejects.
        """
        if identity not in self._pending:
            return
        async with self._session_lock:
            native_id = self._pending.pop(identity, None)
            if native_id is not None:
                await self._save_sessions(
                    {**self._sessions, identity: native_id},
                )

    async def _gate_mcp_tool(
        self,
        identity: str,
        capabilities: HarnessRuntimeCapabilities,
        data: dict[str, Any],
        tool_use_id: str | None,
    ) -> dict[str, Any]:
        """Enforce managed MCP policy even in bypassPermissions mode.

        PreToolUse runs before permission evaluation. Resolve ask here and
        return allow/deny so full access cannot skip, or duplicate, approval.
        """
        tool_name = str(data.get("tool_name") or "")
        for server in sorted(
            capabilities.mcp_servers,
            key=lambda item: len(item.name),
            reverse=True,
        ):
            prefix = f"mcp__{cli_server_name(server.name)}__"
            if not tool_name.startswith(prefix):
                continue
            name = tool_name[len(prefix):]
            effect = server.tool_policies.get(name, server.default_policy)
            if server.tools is not None and name not in server.tools:
                effect = "deny"
            if effect == "ask":
                try:
                    # Return an explicit deny before the CLI hook timeout;
                    # hook errors alone are not a fail-closed policy gate.
                    async with asyncio.timeout(_APPROVAL_TIMEOUT_SECONDS):
                        approval = await self._approve_tool(
                            identity,
                            tool_name,
                            data.get("tool_input") or {},
                            ToolPermissionContext(tool_use_id=tool_use_id),
                        )
                    effect = approval.behavior
                except Exception:  # pylint: disable=broad-except
                    # CancelledError deliberately propagates to turn cleanup.
                    logger.warning(
                        "Claude Code MCP approval failed; denying tool call",
                        exc_info=True,
                    )
                    effect = "deny"
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": effect,
                    "permissionDecisionReason": "QwenPaw MCP tool policy",
                },
            }
        return {}

    async def _approve_tool(
        self,
        identity: str,
        tool_name: str,
        input_data: dict[str, Any],
        permission: ToolPermissionContext,
    ):
        context = self._contexts.get(identity)
        if context is None:
            # A background agent may ask between turns; no live QwenPaw call
            # can route that request to an approver.
            return PermissionResultDeny(
                message="No live QwenPaw call owns this permission request.",
            )
        summary = HarnessApprovalSummary(
            tool_name=tool_name,
            title=permission.display_name or permission.title or tool_name,
            description=permission.description
            or permission.decision_reason
            or tool_name,
            severity="high" if tool_name == "Bash" else "medium",
            parameters={
                "provider": "claude",
                "provider_item_id": permission.tool_use_id,
                "tool_name": tool_name,
                "input": input_data,
                "blocked_path": permission.blocked_path,
            },
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
        finally:
            group.discard(task)
        if decision == "approved":
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message="Rejected by the user.")

    async def _attachment_input(
        self,
        prompt: str,
        cwd: Path,
        attachments: tuple[HarnessAttachment, ...],
    ):
        """Build one SDK user message with images and file mentions."""
        content, references = [], []
        for attachment in attachments:
            if attachment.kind == HarnessAttachmentKind.IMAGE:
                try:
                    raw = await run_io(attachment.path.read_bytes)
                except OSError as exc:
                    raise SDKError(
                        f"Could not read Claude Code attachment: {exc}",
                    ) from exc
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mimetypes.guess_type(
                                attachment.path.name,
                            )[0]
                            or "image/png",
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    },
                )
            else:
                references.append(
                    await run_io(self._file_reference, attachment.path, cwd),
                )
        text = "\n".join(
            part for part in (" ".join(references), prompt) if part
        )
        if text:
            content.insert(0, {"type": "text", "text": text})
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    @staticmethod
    def _file_reference(path, cwd):
        """Return an @file reference portable across host platforms."""
        resolved_path, resolved_cwd = path.expanduser().resolve(
            strict=False,
        ), cwd.expanduser().resolve(strict=False)
        try:
            display = resolved_path.relative_to(resolved_cwd)
        except ValueError:
            display = resolved_path
        text = display.as_posix()
        if any(character.isspace() for character in text):
            return '@"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return f"@{text}"

    async def run_command(
        self,
        context: HarnessCallContext,
        command: str,
        arguments: str,
    ) -> tuple[HarnessEvent, ...]:
        """Drain one slash command for a host without command passthrough.

        A passthrough host never calls this; the CLI parses the raw prompt
        in :meth:`run_turn` instead.
        """
        prompt = f"/{command}" + (f" {arguments}" if arguments else "")
        events = [
            event
            async for event in self.run_turn(
                context,
                HarnessRunRequest(prompt),
            )
        ]
        return tuple(events)

    async def reset_session(self, context: HarnessCallContext) -> None:
        """Disconnect and forget one Claude Code conversation.

        Claude Code's transcript is left in place on the host; only the
        QwenPaw mapping is dropped.
        """
        await self.prepare()
        identity = self._session_key(context)
        async with self._turn_locks.setdefault(identity, asyncio.Lock()):
            async with self._session_lock:
                await finish(self._reset_session(context, identity))

    async def _reset_session(self, context, identity):
        await self._claim_session(context)
        self._pending.pop(identity, None)
        try:
            await self._evict(identity)
        finally:
            # A failed disconnect must not leave the mapping on disk, or the
            # dropped conversation resumes after a restart.
            payload = dict(self._sessions)
            payload.pop(identity, None)
            await self._save_sessions(payload)

    async def close(self) -> None:
        async with self._close_lock:
            self._closed = True
            await finish(self._close())

    async def _close(self):
        tasks = {
            task for group in self._approval_tasks.values() for task in group
        }
        tasks.update(
            task
            for task in self._cli_tasks
            if task is not asyncio.current_task()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        results = await asyncio.gather(
            *(
                self._disconnect(client)
                for client in tuple(self._owned_clients.values())
            ),
            *(reader.close() for reader in tuple(self._readers.values())),
            *(
                self._stop_process(process)
                for process in tuple(self._processes)
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self._clients.clear()
        self._readers.clear()
        self._client_keys.clear()
        self._pending.clear()
        self._contexts.clear()
        self._approval_tasks.clear()
        self._turn_locks.clear()


def _parse_status(output: str) -> dict[str, Any]:
    """Parse `claude auth status --json`, tolerating CLI banner noise."""
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        document = json.loads(output[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return document if isinstance(document, dict) else {}


__all__ = ["ClaudeBackend"]
