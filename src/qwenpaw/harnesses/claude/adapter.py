# -*- coding: utf-8 -*-
"""Claude Code implementation of the third-party agent adapter."""

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

from ...app.approvals import (
    ApprovalRequestSummary,
    get_approval_service,
)
from ...constant import TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS
from ...security.tool_guard.approval import ApprovalDecision
from ...utils.io_utils import (
    read_bytes_async,
    read_json,
    write_json_atomic_async,
)
from ..base import HarnessAdapter, HarnessOperationNotSupportedError
from ..capabilities import HarnessRuntimeCapabilities
from ..events import (
    HarnessAttachment,
    HarnessAttachmentKind,
    HarnessEvent,
    HarnessEventKind,
    HarnessHistoryItem,
    HarnessModel,
    HarnessProvider,
)
from . import environment
from .discovery import (
    ClaudeBinaryResolution,
    resolve_claude_binary_info,
)
from .event_mapper import ClaudeEventMapper, history_items
from .models import (
    DEFAULT_ALIAS,
    catalog as model_catalog,
    from_server_info,
)
from .projection import (
    cli_server_name,
    materialize_skill_plugin,
    mcp_servers,
    tool_policies,
)
from .session_reader import SessionReader

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 30
_STATUS_TIMEOUT_SECONDS = 10
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


class ClaudeAdapter(HarnessAdapter):
    """Run Claude Code SDK sessions inside one QwenPaw agent workspace.

    ``discover_skills`` is deliberately not implemented. Claude Code reports no
    ``skills`` collection in its handshake; skills arrive folded into
    ``commands`` alongside every built-in slash command, with no source or
    scope field to tell them apart. Listing all of those as provider-owned
    Skills would be misleading, so the provider declares
    ``provider_skills_discovery=False`` and inherits the empty default.

    ``run_command`` is likewise absent. The CLI parses slash commands out of
    the prompt, so the provider declares ``passthrough_commands=True`` and the
    runtime hands them to :meth:`run_turn` unchanged — which also keeps a long
    command such as ``/init`` streaming while it works.
    """

    def __init__(
        self,
        state_dir: Path,
        binary: str | None = None,
        env: dict[str, str] | None = None,
        client_factory: Callable[[ClaudeAgentOptions], Any] | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._session_path = state_dir / "claude_sessions.json"
        self._binary = binary
        self._env = dict(env or {})
        self._client_factory = client_factory or ClaudeSDKClient
        self._sessions = self._load_sessions()
        self._pending: dict[str, str] = {}
        self._clients: dict[str, Any] = {}
        self._readers: dict[str, SessionReader] = {}
        self._client_keys: dict[str, tuple[Any, ...]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._session_lock = asyncio.Lock()

    async def status(self) -> HarnessProvider:
        """Return Claude Code installation and authentication status."""
        resolution = self._resolution()
        if resolution is None:
            return HarnessProvider(
                id="claude",
                name="Claude Code",
                available=True,
                installed=False,
            )
        try:
            output = await self._run_cli("auth", "status", "--json")
        except (OSError, asyncio.TimeoutError) as exc:
            return self._provider(resolution, error=str(exc))
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
        return self._provider(
            resolution,
            authenticated=authenticated,
            account=account,
        )

    async def start_login(self, device_code: bool = False) -> dict[str, Any]:
        """Explain why sign-in cannot be driven from the console.

        The CLI prints an OAuth URL and then blocks reading the authorization
        code from its own stdin, and the redirect delivers that code to the
        operator's browser rather than to this host.
        """
        del device_code
        raise HarnessOperationNotSupportedError(_LOGIN_MESSAGE)

    async def logout(self) -> None:
        """Log out through the Claude Code CLI."""
        await self._run_cli("auth", "logout", check=True)

    async def models(self) -> list[HarnessModel]:
        """Return the models Claude Code offers this agent.

        The catalog comes from the CLI handshake, so it already reflects any
        gateway routing in the agent environment and carries each model's real
        effort support. The static alias catalog is the fallback for a CLI that
        cannot be reached.
        """
        try:
            resolution = self._require_resolution()
        except RuntimeError:
            return model_catalog(self._environment())
        client = self._new_client(
            ClaudeAgentOptions(
                cli_path=resolution.path,
                env=dict(self._env),
                setting_sources=[],
            ),
        )
        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                await client.connect()
                info = await client.get_server_info()
        except (OSError, ClaudeSDKError, asyncio.TimeoutError):
            return model_catalog(self._environment())
        finally:
            await client.disconnect()
        return from_server_info(
            (info or {}).get("models"),
        ) or model_catalog(self._environment())

    async def history(self, session_id: str) -> list[HarnessHistoryItem]:
        """Read Claude Code's local JSONL transcript through the SDK."""
        claude_session_id = self._sessions.get(session_id)
        context = self._contexts.get(session_id) or {}
        cwd = context.get("cwd")
        if not claude_session_id:
            return []
        messages = await asyncio.to_thread(
            get_session_messages,
            claude_session_id,
            str(cwd) if cwd else None,
        )
        history: list[HarnessHistoryItem] = []
        for message in messages:
            history.extend(history_items(message))
        return history

    async def run_turn(  # pylint: disable=invalid-overridden-method
        self,
        *,
        session_id: str,
        prompt: str,
        cwd: Path,
        settings: dict[str, Any],
        attachments: list[HarnessAttachment] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        """Send one turn to a persistent Claude Code SDK client."""
        self._contexts[session_id] = {
            "session_id": session_id,
            "cwd": str(cwd),
            **dict(settings.get("_request_context") or {}),
        }
        client, was_reset = await self._client_for_session(
            session_id,
            cwd,
            settings,
        )
        mapper = ClaudeEventMapper()
        try:
            if was_reset:
                yield HarnessEvent(
                    kind=HarnessEventKind.TEXT_DELTA,
                    text=_RESET_NOTICE,
                )
            query_input: str | AsyncIterator[dict[str, Any]] = prompt
            if attachments:
                query_input = self._attachment_input(prompt, cwd, attachments)
            messages = self._readers[session_id].start_turn()
            await client.query(query_input)
            async for message in messages:
                await self._commit_session(session_id)
                for event in mapper.convert(message):
                    yield event
        except (asyncio.CancelledError, GeneratorExit):
            # Interrupt leaves a terminal result in the SDK receive queue.
            # Evict synchronously so no later turn can consume that result.
            reader = None
            if self._clients.get(session_id) is client:
                self._clients.pop(session_id, None)
                reader = self._readers.pop(session_id, None)
                self._client_keys.pop(session_id, None)
                self._pending.pop(session_id, None)
            await asyncio.shield(self._close_cancelled_client(client, reader))
            raise

    @staticmethod
    async def _close_cancelled_client(
        client: Any,
        reader: SessionReader | None,
    ) -> None:
        """Best-effort cleanup must not replace the caller's cancellation."""
        operations = [client.interrupt, client.disconnect]
        if reader is not None:
            operations.append(reader.close)
        for operation in operations:
            try:
                async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
                    await operation()
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Claude cancellation cleanup failed", exc_info=True,
                )

    async def _commit_session(self, session_id: str) -> None:
        """Persist the Claude session id once the CLI has answered.

        Recording it at connect time is too early: Claude Code only writes the
        transcript this id names once a turn produces output, so a process that
        dies mid-first-turn would leave a mapping that every later ``--resume``
        rejects.
        """
        claude_session_id = self._pending.pop(session_id, None)
        if claude_session_id is None:
            return
        self._sessions[session_id] = claude_session_id
        await write_json_atomic_async(self._session_path, self._sessions)

    async def _attachment_input(
        self,
        prompt: str,
        cwd: Path,
        attachments: list[HarnessAttachment],
    ) -> AsyncIterator[dict[str, Any]]:
        """Build one SDK user message with images and file mentions."""
        content: list[dict[str, Any]] = []
        file_references: list[str] = []
        for attachment in attachments:
            if attachment.kind == HarnessAttachmentKind.IMAGE:
                image_data = await read_bytes_async(attachment.path)
                media_type = (
                    mimetypes.guess_type(attachment.path.name)[0]
                    or "image/png"
                )
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_data).decode(
                                "ascii",
                            ),
                        },
                    },
                )
                continue
            file_references.append(
                self._file_reference(attachment.path, cwd),
            )
        text = "\n".join(
            part for part in (" ".join(file_references), prompt) if part
        )
        if text:
            content.insert(0, {"type": "text", "text": text})
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
            "parent_tool_use_id": None,
        }

    @staticmethod
    def _file_reference(path: Path, cwd: Path) -> str:
        """Return an @file reference portable across host platforms."""
        resolved_path = path.expanduser().resolve(strict=False)
        resolved_cwd = cwd.expanduser().resolve(strict=False)
        try:
            display_path = resolved_path.relative_to(resolved_cwd)
        except ValueError:
            display_path = resolved_path
        path_text = display_path.as_posix()
        if any(character.isspace() for character in path_text):
            escaped_path = path_text.replace("\\", "\\\\").replace(
                '"',
                '\\"',
            )
            return f'@"{escaped_path}"'
        return f"@{path_text}"

    async def reset_session(self, session_id: str) -> None:
        """Disconnect and forget one Claude Code conversation.

        Claude Code exposes no transcript deletion API, so the provider-side
        session file is left in place and only the QwenPaw mapping is dropped.
        """
        async with self._session_lock:
            client = self._clients.pop(session_id, None)
            reader = self._readers.pop(session_id, None)
            self._client_keys.pop(session_id, None)
            self._contexts.pop(session_id, None)
            self._sessions.pop(session_id, None)
            self._pending.pop(session_id, None)
            try:
                if client is not None:
                    await client.disconnect()
                if reader is not None:
                    await reader.close()
            finally:
                # A failed disconnect must not leave the mapping on disk, or
                # the dropped conversation resumes after a restart.
                await write_json_atomic_async(
                    self._session_path,
                    self._sessions,
                )

    async def stop(self) -> None:
        """Disconnect every SDK client owned by this adapter."""
        async with self._session_lock:
            for client in tuple(self._clients.values()):
                await client.disconnect()
            for reader in tuple(self._readers.values()):
                await reader.close()
            self._clients.clear()
            self._readers.clear()
            self._client_keys.clear()
            self._pending.clear()

    async def _client_for_session(
        self,
        session_id: str,
        cwd: Path,
        settings: dict[str, Any],
    ) -> tuple[Any, bool]:
        """Return a connected client, and whether its thread was restarted."""
        key = self._client_key(cwd, settings)
        existing = self._reusable_client(session_id, key)
        if existing is not None:
            return existing, False
        capabilities = settings.get("_runtime_capabilities")
        if not isinstance(capabilities, HarnessRuntimeCapabilities):
            capabilities = HarnessRuntimeCapabilities()
        plugin_path = await asyncio.to_thread(
            materialize_skill_plugin,
            self._state_dir,
            capabilities,
        )
        async with self._session_lock:
            existing = self._reusable_client(session_id, key)
            if existing is not None:
                return existing, False
            stale = self._clients.pop(session_id, None)
            stale_reader = self._readers.pop(session_id, None)
            if stale is not None:
                await stale.disconnect()
            if stale_reader is not None:
                await stale_reader.close()
            resumable = self._sessions.get(session_id)
            is_resume = bool(resumable)
            claude_session_id = (
                resumable
                or self._pending.get(session_id)
                or str(uuid.uuid4())
            )
            was_reset = False
            try:
                client = await self._connect(
                    session_id=session_id,
                    claude_session_id=claude_session_id,
                    is_resume=is_resume,
                    cwd=cwd,
                    settings=settings,
                    plugin_path=plugin_path,
                )
            except ProcessError as exc:
                if not is_resume or _MISSING_CONVERSATION not in str(exc):
                    raise
                self._sessions.pop(session_id, None)
                await write_json_atomic_async(
                    self._session_path,
                    self._sessions,
                )
                claude_session_id = str(uuid.uuid4())
                was_reset = True
                client = await self._connect(
                    session_id=session_id,
                    claude_session_id=claude_session_id,
                    is_resume=False,
                    cwd=cwd,
                    settings=settings,
                    plugin_path=plugin_path,
                )
            if not is_resume or was_reset:
                self._pending[session_id] = claude_session_id
            self._clients[session_id] = client
            self._readers[session_id] = SessionReader(client)
            self._client_keys[session_id] = key
            return client, was_reset

    def _reusable_client(
        self,
        session_id: str,
        key: tuple[Any, ...],
    ) -> Any | None:
        """Return the session's client if its settings match and it is live."""
        client = self._clients.get(session_id)
        reader = self._readers.get(session_id)
        if (
            client is None
            or reader is None
            or reader.closed
            or self._client_keys.get(session_id) != key
        ):
            return None
        return client

    async def _connect(
        self,
        *,
        session_id: str,
        claude_session_id: str,
        is_resume: bool,
        cwd: Path,
        settings: dict[str, Any],
        plugin_path: Path | None,
    ) -> Any:
        """Build and connect one SDK client."""
        options = self._options(
            session_id=session_id,
            claude_session_id=claude_session_id,
            is_resume=is_resume,
            cwd=cwd,
            settings=settings,
            plugin_path=plugin_path,
        )
        client = self._new_client(options)
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            await client.connect()
        return client

    def _options(
        self,
        *,
        session_id: str,
        claude_session_id: str,
        is_resume: bool,
        cwd: Path,
        settings: dict[str, Any],
        plugin_path: Path | None,
    ) -> ClaudeAgentOptions:
        permission_mode = str(settings.get("permission_mode") or "default")
        effort = settings.get("reasoning_effort")
        capabilities = settings.get("_runtime_capabilities")
        if not isinstance(capabilities, HarnessRuntimeCapabilities):
            capabilities = HarnessRuntimeCapabilities()
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
            cli_path=self._require_resolution().path,
            cwd=cwd,
            env=dict(self._env),
            resume=claude_session_id if is_resume else None,
            session_id=None if is_resume else claude_session_id,
            model=None if model in ("", DEFAULT_ALIAS) else model,
            extra_args=extra_args,
            include_partial_messages=True,
            permission_mode=permission_mode,
            can_use_tool=lambda tool_name, input_data, context: (
                self._approve_tool(
                    session_id,
                    tool_name,
                    input_data,
                    context,
                )
            ),
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[
                            lambda data, tool_use_id, context: (
                                self._gate_mcp_tool(
                                    session_id,
                                    capabilities,
                                    data,
                                    tool_use_id,
                                )
                            ),
                        ],
                        timeout=TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS + 30,
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

    async def _gate_mcp_tool(
        self,
        session_id: str,
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
                    async with asyncio.timeout(
                        TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS,
                    ):
                        approval = await self._approve_tool(
                            session_id,
                            tool_name,
                            data.get("tool_input") or {},
                            ToolPermissionContext(tool_use_id=tool_use_id),
                        )
                    effect = approval.behavior
                except Exception:  # pylint: disable=broad-except
                    # CancelledError deliberately propagates to turn cleanup.
                    logger.warning(
                        "Claude MCP approval failed; denying tool call",
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
        session_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        request_context = self._contexts.get(session_id) or {}
        detail = context.description or context.decision_reason or tool_name
        summary = ApprovalRequestSummary(
            source_type="claude",
            name=context.display_name or context.title or tool_name,
            severity="high" if tool_name == "Bash" else "medium",
            result_summary=detail,
            payload={
                "provider": "claude",
                "provider_item_id": context.tool_use_id,
                "tool_name": tool_name,
                "input": input_data,
                "blocked_path": context.blocked_path,
            },
        )
        service = get_approval_service()
        pending = await service.create_pending_summary(
            session_id=session_id,
            root_session_id=session_id,
            owner_agent_id=str(
                request_context.get("agent_id") or "default",
            ),
            user_id=str(request_context.get("user_id") or "default"),
            channel=str(request_context.get("channel") or "console"),
            agent_id=str(request_context.get("agent_id") or "default"),
            summary=summary,
        )
        decision = await service.wait_for_approval(
            pending.request_id,
            pending.timeout_seconds,
        )
        if decision == ApprovalDecision.APPROVED:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message="Rejected by the user.")

    async def _run_cli(self, *arguments: str, check: bool = False) -> str:
        resolution = self._require_resolution()
        process = await asyncio.create_subprocess_exec(
            str(resolution.path),
            *arguments,
            env=self._environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(_STATUS_TIMEOUT_SECONDS):
                stdout, _ = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await asyncio.shield(process.wait())
            raise
        output = stdout.decode(errors="replace")
        if check and process.returncode != 0:
            raise RuntimeError(
                f"claude {' '.join(arguments)} failed "
                f"(exit {process.returncode}): {output.strip()}",
            )
        return output

    def _environment(self) -> dict[str, str]:
        return environment.resolve(self._env)

    def _resolution(self) -> ClaudeBinaryResolution | None:
        return resolve_claude_binary_info(self._binary)

    def _require_resolution(self) -> ClaudeBinaryResolution:
        resolution = self._resolution()
        if resolution is None:
            raise RuntimeError("Claude Code CLI executable was not found.")
        return resolution

    def _provider(
        self,
        resolution: ClaudeBinaryResolution,
        *,
        authenticated: bool = False,
        account: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> HarnessProvider:
        return HarnessProvider(
            id="claude",
            name="Claude Code",
            available=True,
            installed=True,
            authenticated=authenticated,
            account=account,
            runtime_path=str(resolution.path),
            runtime_source=resolution.source,
            error=error,
        )

    def _new_client(self, options: ClaudeAgentOptions) -> Any:
        return self._client_factory(options)

    def _client_key(
        self,
        cwd: Path,
        settings: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            str(cwd.resolve()),
            str(settings.get("model") or ""),
            str(settings.get("reasoning_effort") or ""),
            str(settings.get("permission_mode") or "default"),
            environment.fingerprint(self._env),
            _runtime_capability_fingerprint(settings),
        )

    def _load_sessions(self) -> dict[str, str]:
        try:
            raw = read_json(self._session_path)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): str(value) for key, value in raw.items() if key and value
        }


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


def _runtime_capability_fingerprint(
    settings: dict[str, Any],
) -> str:
    capabilities = settings.get("_runtime_capabilities")
    if isinstance(capabilities, HarnessRuntimeCapabilities):
        return capabilities.fingerprint
    return ""


__all__ = ["ClaudeAdapter"]
