# -*- coding: utf-8 -*-
"""Unit tests for ``qwenpaw.app.agent_avatar``."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from qwenpaw.app import agent_avatar
from qwenpaw.app.agent_avatar import render_avatar_svg

_NS = "{http://www.w3.org/2000/svg}"


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _stops(root: ET.Element) -> list[str]:
    return [stop.get("stop-color") for stop in root.iter(f"{_NS}stop")]


def _glyph(root: ET.Element) -> ET.Element:
    return root.find(f"{_NS}g")


@pytest.mark.parametrize("icon", [None, "", "🦊", "telescope", "lucid:x:y"])
def test_non_lucide_icons_have_no_avatar(icon):
    assert render_avatar_svg(icon) is None


def test_renders_gradient_tile_with_white_glyph():
    root = _parse(render_avatar_svg("lucide:telescope:indigo"))

    assert root.get("viewBox") == "0 0 24 24"
    assert _stops(root) == ["#818cf8", "#4f46e5"]
    glyph = _glyph(root)
    assert glyph.get("stroke") == "#fff"
    assert glyph.get("fill") == "none"
    # Same glyph share as the console badge: 64% of the tile, centred.
    assert glyph.get("transform") == "translate(4.32 4.32) scale(0.64)"
    icons = json.loads(agent_avatar._ICONS_PATH.read_text(encoding="utf-8"))
    assert len(list(glyph)) == len(icons["icons"]["telescope"])


def test_unknown_parts_fall_back_to_bot_and_default_color():
    fallback = _parse(render_avatar_svg("lucide:no-such-icon:no-such-color"))
    bot_blue = _parse(render_avatar_svg("lucide:bot:blue"))

    assert _stops(fallback) == _stops(bot_blue)
    assert ET.tostring(_glyph(fallback)) == ET.tostring(_glyph(bot_blue))


def test_current_color_fills_become_white():
    svg = render_avatar_svg("lucide:palette:pink")

    assert "currentColor" not in svg
    assert any(
        el.get("fill") == "#fff" for el in _glyph(_parse(svg)).iter()
    )


def test_icon_set_covers_every_preset_with_valid_shapes():
    icons = json.loads(agent_avatar._ICONS_PATH.read_text(encoding="utf-8"))

    assert icons["defaultColor"] in icons["colors"]
    assert "bot" in icons["icons"]
    for name, nodes in icons["icons"].items():
        assert nodes, name
        for tag, attrs in nodes:
            assert tag in {"path", "circle", "rect", "line", "ellipse"}
            assert "key" not in attrs
