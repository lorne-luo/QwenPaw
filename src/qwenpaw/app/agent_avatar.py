# -*- coding: utf-8 -*-
"""Render an agent's lucide avatar as a standalone SVG.

The console stores avatars as ``lucide:<name>:<color>`` and draws them as a
white lucide glyph on a gradient tile. Native clients cannot run lucide, so
the server draws the same tile from ``agent_avatar_icons.json``, which
``console/scripts/export-agent-icons.mjs`` generates from the console's
preset list. The tile fills the whole canvas; clients clip it to a circle or
a rounded square themselves.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

_ICONS_PATH = Path(__file__).with_name("agent_avatar_icons.json")
_PREFIX = "lucide:"
_FALLBACK_ICON = "bot"
# Glyph share of the tile and its stroke, matching the console's AgentAvatar.
_GLYPH_SCALE = 0.64
_STROKE_WIDTH = 2.25
_VIEWBOX = 24
_PIXELS = 96


@lru_cache(maxsize=1)
def _icon_set() -> dict[str, Any]:
    return json.loads(_ICONS_PATH.read_text(encoding="utf-8"))


def _element(tag: str, attrs: dict[str, str]) -> str:
    rendered = "".join(
        f" {name}={quoteattr('#fff' if value == 'currentColor' else value)}"
        for name, value in attrs.items()
    )
    return f"<{tag}{rendered}/>"


def render_avatar_svg(icon: str | None) -> str | None:
    """Return the avatar SVG, or ``None`` when ``icon`` is not lucide.

    Unknown icon names fall back to the bot glyph and unknown colors to the
    default color, the same way the console renders them.
    """
    if not icon or not icon.startswith(_PREFIX):
        return None
    name, _, color = icon[len(_PREFIX) :].partition(":")
    icon_set = _icon_set()
    nodes = icon_set["icons"].get(name) or icon_set["icons"][_FALLBACK_ICON]
    colors = icon_set["colors"]
    start, end = colors.get(color) or colors[icon_set["defaultColor"]]

    offset = _VIEWBOX * (1 - _GLYPH_SCALE) / 2
    glyph = "".join(_element(tag, attrs) for tag, attrs in nodes)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{_PIXELS}" height="{_PIXELS}"'
        f' viewBox="0 0 {_VIEWBOX} {_VIEWBOX}">'
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f"<stop offset=\"0\" stop-color={quoteattr(start)}/>"
        f"<stop offset=\"1\" stop-color={quoteattr(end)}/>"
        "</linearGradient></defs>"
        f'<rect width="{_VIEWBOX}" height="{_VIEWBOX}" fill="url(#bg)"/>'
        f'<g transform="translate({offset:g} {offset:g})'
        f' scale({_GLYPH_SCALE:g})"'
        ' fill="none" stroke="#fff"'
        f' stroke-width="{_STROKE_WIDTH:g}"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        f"{glyph}</g></svg>"
    )
