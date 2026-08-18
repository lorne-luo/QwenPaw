# -*- coding: utf-8 -*-
"""Claude Code model catalogs.

The CLI reports its resolved catalog in the handshake, which is what
:func:`from_server_info` maps. :func:`catalog` is the offline fallback derived
from the alias environment variables alone.
"""

from __future__ import annotations

from typing import Mapping

from ..events import HarnessModel

DEFAULT_ALIAS = "default"
ALIASES = ("fable", "opus", "sonnet", "haiku")
CLI_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORTS_WITHOUT_MAX = ("low", "medium", "high", "xhigh")

_ALIAS_NAMES = {
    "fable": "Fable",
    "opus": "Opus",
    "sonnet": "Sonnet",
    "haiku": "Haiku",
}
_ALIAS_DESCRIPTIONS = {
    "fable": "Latest Fable model",
    "opus": "Latest Opus model",
    "sonnet": "Latest Sonnet model",
    "haiku": "Latest Haiku model",
}


def catalog(env: Mapping[str, str] | None = None) -> list[HarnessModel]:
    """Return the alias catalog Claude Code resolves for this environment.

    This is the offline fallback for :func:`from_server_info`, used when the
    CLI cannot be reached. It offers the aliases the CLI understands and lets
    the CLI resolve them; the routing variables only decide how each alias is
    labelled and which reasoning efforts are advertised.
    """
    values = env or {}
    models = [
        HarnessModel(
            id=DEFAULT_ALIAS,
            name="Default",
            description="Let Claude Code choose its configured model",
            is_default=True,
        ),
    ]
    for alias in ALIASES:
        resolved = str(values.get(_model_variable(alias)) or "").strip()
        models.append(
            HarnessModel(
                id=alias,
                name=_ALIAS_NAMES[alias],
                description=resolved or _ALIAS_DESCRIPTIONS[alias],
                reasoning_efforts=list(
                    _efforts(values.get(_capabilities_variable(alias))),
                ),
            ),
        )
    return models


def from_server_info(entries: object) -> list[HarnessModel]:
    """Map Claude Code's handshake model catalog to picker entries.

    The CLI resolves aliases before reporting them, so ``resolvedModel``
    already names the gateway model when the agent environment routes one, and
    ``supportedEffortLevels`` is authoritative per model — the static catalog
    can only guess it.
    """
    if not isinstance(entries, list):
        return []
    models: list[HarnessModel] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("value") or "").strip()
        if not model_id:
            continue
        resolved = str(item.get("resolvedModel") or "").strip()
        efforts = (
            item.get("supportedEffortLevels")
            if item.get("supportsEffort")
            else None
        )
        models.append(
            HarnessModel(
                id=model_id,
                name=str(item.get("displayName") or model_id),
                description=_describe(
                    resolved,
                    str(item.get("description") or ""),
                    model_id,
                ),
                is_default=model_id == DEFAULT_ALIAS,
                reasoning_efforts=[
                    str(value) for value in (efforts or []) if value
                ],
            ),
        )
    if models and not any(model.is_default for model in models):
        models[0].is_default = True
    return models


def _describe(resolved: str, description: str, model_id: str) -> str:
    """Lead with the model an entry actually resolves to.

    Under a gateway the CLI keeps its own generic wording ("Custom Opus
    model"), so the resolved name is the only place the operator can see which
    model an alias really reaches.
    """
    if not resolved or resolved == model_id:
        return description
    if not description:
        return resolved
    return f"{resolved} · {description}"


def _model_variable(alias: str) -> str:
    return f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL"


def _capabilities_variable(alias: str) -> str:
    return f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL_SUPPORTED_CAPABILITIES"


def _efforts(capabilities: str | None) -> tuple[str, ...]:
    """Return the reasoning efforts one alias accepts.

    An unset variable means the alias is served by Anthropic directly, where
    the CLI accepts every effort level. A declared capability list is
    authoritative: gateways that omit ``effort`` support none.
    """
    if capabilities is None:
        return CLI_EFFORTS
    declared = {
        item.strip() for item in str(capabilities).split(",") if item.strip()
    }
    if "effort" not in declared:
        return ()
    if "max_effort" in declared:
        return CLI_EFFORTS
    return _EFFORTS_WITHOUT_MAX


__all__ = [
    "ALIASES",
    "CLI_EFFORTS",
    "DEFAULT_ALIAS",
    "catalog",
    "from_server_info",
]
