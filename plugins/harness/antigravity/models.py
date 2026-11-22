# -*- coding: utf-8 -*-
"""Model choices read from an ACP session's models and config options."""

from qwenpaw.plugins.harnesses import HarnessModel

# A nonempty id keeps generic model pickers from treating the CLI default as
# "nothing selected"; the adapter never sends it to agy.
DEFAULT_MODEL_ID = "default"


def thought_levels(result):
    """Values of the session's ``thought_level`` select option."""
    for option in (result or {}).get("configOptions") or ():
        if isinstance(option, dict) and option.get("id") == "thought_level":
            return tuple(
                str(item["value"])
                for item in option.get("options") or ()
                if isinstance(item, dict) and item.get("value")
            )
    return ()


def model_ids(result):
    """Selectable ``session/set_model`` ids; each names its effort."""
    models = (result or {}).get("models") or {}
    return tuple(
        (str(item["modelId"]), str(item.get("name") or item["modelId"]))
        for item in models.get("availableModels") or ()
        if isinstance(item, dict) and item.get("modelId")
    )


def default_model(efforts=(), description=""):
    """The CLI default model; only it takes a separate thinking level."""
    return HarnessModel(
        id=DEFAULT_MODEL_ID,
        name="Antigravity default",
        description=description,
        is_default=True,
        reasoning_efforts=tuple(efforts),
    )


def catalog(result):
    """HarnessModels for a ``session/new`` or ``session/resume`` result."""
    return (
        default_model(thought_levels(result)),
        *(HarnessModel(id=key, name=name) for key, name in model_ids(result)),
    )
