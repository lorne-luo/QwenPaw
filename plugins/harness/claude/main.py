# -*- coding: utf-8 -*-
"""Optional Claude Code registration; clients open only on real calls."""

from qwenpaw.plugins.harnesses import (
    HarnessApprovalPreset,
    HarnessCapabilities,
    HarnessCommand,
    HarnessSpec,
)


async def create_backend(context, settings):
    from .adapter import ClaudeBackend

    backend = ClaudeBackend(context, settings)
    await backend.prepare()
    return backend


class Plugin:
    def register(self, context):
        context.register_harness(
            HarnessSpec(
                id="claude",
                label="Claude Code",
                create=create_backend,
                # Per-agent ``env`` travels with every call instead, so
                # editing it never replaces a busy backend.
                instance_settings_keys=("binary",),
                capabilities=HarnessCapabilities(
                    authentication=True,
                    model_selection=True,
                    reasoning_effort=True,
                    reasoning_stream=True,
                    tool_stream=True,
                    session_resume=True,
                    attachments=True,
                    qwenpaw_skills_projection=True,
                    qwenpaw_mcp_projection=True,
                    mcp_tool_allowlist=True,
                    passthrough_commands=True,
                    # Claude Code parses slash commands itself and reports
                    # 175 of them, including every Skill on the host. These
                    # are the built-ins worth surfacing in the console;
                    # anything else the operator types still reaches the CLI.
                    # `model` and `config` are omitted because they change
                    # CLI-side state that QwenPaw's own agent settings would
                    # then disagree with, and `clear` is owned by the host.
                    commands=(
                        HarnessCommand(
                            "init",
                            "Create or refresh CLAUDE.md with codebase "
                            "documentation",
                        ),
                        HarnessCommand(
                            "compact",
                            "Compact the current Claude Code session",
                            accepts_arguments=True,
                        ),
                        HarnessCommand(
                            "context",
                            "Show current context usage",
                        ),
                        HarnessCommand(
                            "usage",
                            "Show session cost and plan usage",
                        ),
                        HarnessCommand(
                            "doctor",
                            "Health-check the Claude Code installation",
                        ),
                        HarnessCommand(
                            "mcp",
                            "Manage MCP servers for this session",
                            accepts_arguments=True,
                        ),
                    ),
                    approval_presets=(
                        HarnessApprovalPreset(
                            "ask",
                            "Ask before actions",
                            "Ask before file changes and command execution.",
                            {"permission_mode": "default"},
                        ),
                        HarnessApprovalPreset(
                            "accept-edits",
                            "Accept edits",
                            "Allow file edits while keeping other safeguards.",
                            {"permission_mode": "acceptEdits"},
                        ),
                        HarnessApprovalPreset(
                            "plan",
                            "Plan only",
                            "Analyze and plan without changing files.",
                            {"permission_mode": "plan"},
                        ),
                        HarnessApprovalPreset(
                            "auto",
                            "Automatic",
                            "Let Claude Code decide which safe actions can "
                            "run.",
                            {"permission_mode": "auto"},
                        ),
                        HarnessApprovalPreset(
                            "full-access",
                            "Full access",
                            "Skip permission checks in a trusted workspace.",
                            {"permission_mode": "bypassPermissions"},
                        ),
                    ),
                ),
                config_fields=(
                    {
                        "name": "binary",
                        "type": "text",
                        "label": "Claude Code executable",
                        "help": "Optional standalone claude CLI path.",
                    },
                ),
                description=(
                    "Claude Code SDK with per-agent environment variables."
                ),
            ),
        )


plugin = Plugin()
