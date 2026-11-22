# -*- coding: utf-8 -*-
"""Optional Antigravity registration; agy starts only on real calls."""

from qwenpaw.plugins.harnesses import (
    HarnessApprovalPreset,
    HarnessCapabilities,
    HarnessSpec,
)


async def create_backend(context, settings):
    from .adapter import AntigravityBackend

    backend = AntigravityBackend(context, settings)
    await backend.prepare()
    return backend


class Plugin:
    def register(self, context):
        context.register_harness(
            HarnessSpec(
                id="antigravity",
                label="Antigravity",
                create=create_backend,
                instance_settings_keys=("binary",),
                capabilities=HarnessCapabilities(
                    model_selection=True,
                    reasoning_effort=True,
                    reasoning_stream=True,
                    tool_stream=True,
                    session_resume=True,
                    approval_presets=(
                        HarnessApprovalPreset(
                            "configured",
                            "Ask before actions",
                            "Follow the CLI permission settings and ask "
                            "here when Antigravity requests approval.",
                            {"permission_mode": "configured"},
                        ),
                        HarnessApprovalPreset(
                            "accept-edits",
                            "Accept edits",
                            "Approve workspace file edits without asking.",
                            {"permission_mode": "accept-edits"},
                        ),
                        HarnessApprovalPreset(
                            "plan",
                            "Plan",
                            "Plan carefully before executing a task.",
                            {"permission_mode": "plan"},
                        ),
                        HarnessApprovalPreset(
                            "full-access",
                            "Full access",
                            "Approve every tool permission request in a "
                            "trusted workspace.",
                            {"permission_mode": "full-access"},
                        ),
                    ),
                ),
                config_fields=(
                    {
                        "name": "binary",
                        "type": "text",
                        "label": "Antigravity executable",
                        "help": "Optional agy CLI path; Linux only.",
                    },
                ),
                description=(
                    "Native Antigravity CLI (agy) over the Agent Client "
                    "Protocol."
                ),
            ),
        )


plugin = Plugin()
