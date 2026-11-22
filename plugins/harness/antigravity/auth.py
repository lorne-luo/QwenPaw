# -*- coding: utf-8 -*-
"""Bounded ACP compatibility and authentication probe."""

import asyncio
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Literal

from .acp import AcpClient, AcpError

PROBE_TIMEOUT = 15


@dataclass(frozen=True)
class AuthResult:
    state: Literal["authenticated", "unauthenticated", "unknown"]
    reason: str | None = None
    compatible: bool = True


async def probe_auth(binary, env):
    """Initialize ``agy acp`` and list sessions; no model request is made.

    ``session/list`` answers "Authentication required" without a login and
    creates no conversation, unlike ``session/new``.
    """
    client = None
    try:
        with TemporaryDirectory(prefix="qwenpaw-agy-auth-") as cwd:
            try:
                async with asyncio.timeout(PROBE_TIMEOUT):
                    client = await AcpClient.start(
                        [binary, "acp"],
                        cwd,
                        {**env, "BROWSER": "/bin/false"},
                    )
                    capabilities = client.agent.get("agentCapabilities") or {}
                    sessions = capabilities.get("sessionCapabilities") or {}
                    if "resume" not in sessions:
                        return AuthResult(
                            "unknown",
                            "agy acp cannot resume sessions; upgrade "
                            "Antigravity CLI",
                            compatible=False,
                        )
                    try:
                        await client.request("session/list", {"cwd": cwd})
                    except AcpError as exc:
                        if exc.auth_required:
                            return AuthResult(
                                "unauthenticated",
                                "Antigravity CLI requires login.",
                            )
                        raise
                    return AuthResult("authenticated")
            finally:
                if client is not None:
                    await asyncio.shield(client.close())
    except TimeoutError:
        return AuthResult(
            "unknown",
            "Antigravity authentication probe timed out.",
        )
    except AcpError:
        # A server that cannot even initialize is not ACP-compatible.
        compatible = client is not None and bool(client.agent)
        return AuthResult(
            "unknown",
            "Antigravity authentication could not be verified."
            if compatible
            else "agy acp did not start; upgrade Antigravity CLI",
            compatible=compatible,
        )
    except Exception:  # pylint: disable=broad-except
        return AuthResult(
            "unknown",
            "Antigravity authentication could not be verified.",
        )
