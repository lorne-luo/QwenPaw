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
                    tool_stream=True,
                    session_resume=True,
                    approval_presets=(
                        HarnessApprovalPreset(
                            "configured",
                            "CLI configuration",
                            "Use existing CLI permissions; "
                            "no approval dialog.",
                            {"permission_mode": "configured"},
                        ),
                        HarnessApprovalPreset(
                            "full-access",
                            "Full access",
                            "Skip tool permission checks in a trusted "
                            "workspace.",
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
                    "Native Antigravity CLI (agy) over streaming NDJSON."
                ),
            ),
        )


plugin = Plugin()
