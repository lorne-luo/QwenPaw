# -*- coding: utf-8 -*-
"""Tests for the Claude Code alias model catalog."""

from __future__ import annotations

from qwenpaw.harnesses.claude.models import catalog, from_server_info

_GATEWAY = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "gpt-luna-max",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES": (
        "1m,effort,max_effort,thinking"
    ),
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "openbest",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES": "1m,thinking",
}


def _by_id(models, model_id):
    return next(model for model in models if model.id == model_id)


def test_catalog_offers_every_alias_with_default_first() -> None:
    models = catalog({})

    assert [model.id for model in models] == [
        "default",
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert models[0].is_default is True
    assert sum(model.is_default for model in models) == 1


def test_plain_aliases_accept_every_cli_effort() -> None:
    opus = _by_id(catalog({}), "opus")

    assert opus.description == "Latest Opus model"
    assert opus.reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_default_alias_carries_no_reasoning_efforts() -> None:
    assert _by_id(catalog({}), "default").reasoning_efforts == []


def test_gateway_model_is_named_and_keeps_the_alias_as_id() -> None:
    opus = _by_id(catalog(_GATEWAY), "opus")

    assert opus.id == "opus"
    assert opus.description == "gpt-luna-max"


def test_declared_max_effort_keeps_the_full_effort_range() -> None:
    opus = _by_id(catalog(_GATEWAY), "opus")

    assert opus.reasoning_efforts == ["low", "medium", "high", "xhigh", "max"]


def test_effort_without_max_effort_drops_the_max_level() -> None:
    models = catalog(
        {
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "openlite",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES": (
                "1m,effort"
            ),
        },
    )

    assert _by_id(models, "haiku").reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]


def test_capabilities_without_effort_offer_no_reasoning_efforts() -> None:
    sonnet = _by_id(catalog(_GATEWAY), "sonnet")

    assert sonnet.description == "openbest"
    assert sonnet.reasoning_efforts == []


def test_unrouted_aliases_stay_plain_when_others_are_routed() -> None:
    fable = _by_id(catalog(_GATEWAY), "fable")

    assert fable.description == "Latest Fable model"
    assert fable.reasoning_efforts == ["low", "medium", "high", "xhigh", "max"]


def test_blank_model_variable_falls_back_to_the_plain_label() -> None:
    opus = _by_id(catalog({"ANTHROPIC_DEFAULT_OPUS_MODEL": "   "}), "opus")

    assert opus.description == "Latest Opus model"


_SERVER_INFO = [
    {
        "value": "default",
        "resolvedModel": "claude-opus-5[1m]",
        "displayName": "Default (recommended)",
        "description": "Opus 5 with 1M context",
        "supportsEffort": True,
        "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
    },
    {
        "value": "haiku",
        "resolvedModel": "claude-haiku-4-5-20251001",
        "displayName": "Haiku",
        "supportsEffort": None,
        "supportedEffortLevels": None,
    },
]


def test_server_info_catalog_keeps_cli_ids_and_effort_support() -> None:
    models = from_server_info(_SERVER_INFO)

    assert [model.id for model in models] == ["default", "haiku"]
    assert models[0].is_default is True
    assert models[0].name == "Default (recommended)"
    assert models[0].description == (
        "claude-opus-5[1m] · Opus 5 with 1M context"
    )
    assert models[0].reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert models[1].reasoning_efforts == []


def test_server_info_leads_with_the_resolved_gateway_model() -> None:
    models = from_server_info(
        [
            {
                "value": "opus",
                "resolvedModel": "gpt-luna-max",
                "description": "Custom Opus model",
            },
            {"value": "sonnet", "resolvedModel": "openbest"},
            {"value": "haiku", "resolvedModel": "haiku"},
        ],
    )

    assert models[0].description == "gpt-luna-max · Custom Opus model"
    assert models[0].name == "opus"
    assert models[1].description == "openbest"
    # A self-resolving id adds nothing, so it is not repeated.
    assert models[2].description == ""


def test_server_info_marks_a_default_when_the_cli_names_none() -> None:
    models = from_server_info([{"value": "sonnet"}, {"value": "haiku"}])

    assert models[0].is_default is True
    assert models[1].is_default is False


def test_server_info_drops_unusable_entries() -> None:
    models = from_server_info(
        [{"value": ""}, {"value": "   "}, "not a dict", {"value": "opus"}],
    )

    assert [model.id for model in models] == ["opus"]


def test_server_info_tolerates_a_missing_or_wrong_shaped_payload() -> None:
    assert from_server_info(None) == []
    assert from_server_info({}) == []
    assert from_server_info([]) == []
