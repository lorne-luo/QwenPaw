# -*- coding: utf-8 -*-
"""Regression tests for the ``/skill`` no-input fallback behavior.

Covers the frontmatter opt-in ``invoke_without_input: true`` added to the
workspace skill fallback handler in ``runtime.builtin_commands``:

- Default behavior is unchanged: ``/<skill>`` with no input returns the
  details card instead of invoking.
- A skill whose ``SKILL.md`` frontmatter sets ``invoke_without_input: true``
  runs its default workflow even with no user-supplied input: the skill body
  is injected into the last input message and the handler returns ``None``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.runtime.builtin_commands import _skill_fallback_handler

from agentscope.message import Msg, TextBlock


def _skill_md(name: str, description: str, *, invoke_without_input: bool) -> str:
    opt = "invoke_without_input: true\n" if invoke_without_input else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{opt}"
        "---\n\n"
        "# Test Skill\n\n"
        "Run the default workflow when invoked.\n"
    )


def _make_ctx(workspace_dir: Path, query: str) -> SimpleNamespace:
    message = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text=query)],
    )
    return SimpleNamespace(
        workspace=SimpleNamespace(workspace_dir=workspace_dir),
        input_msgs=[message],
        request=None,
    )


def _setup_workspace(tmp_path: Path, skill_md: str) -> Path:
    """Create a workspace with one enabled skill named ``demo-skill``."""
    from qwenpaw.agents.skill_system.registry import (
        get_workspace_skills_dir,
        reconcile_workspace_manifest,
    )

    workspace_dir = tmp_path / "workspaces" / "demo"
    skills_dir = get_workspace_skills_dir(workspace_dir)
    (skills_dir / "demo-skill").mkdir(parents=True)
    (skills_dir / "demo-skill" / "SKILL.md").write_text(
        skill_md,
        encoding="utf-8",
    )
    manifest = reconcile_workspace_manifest(workspace_dir)
    manifest.setdefault("skills", {})
    manifest["skills"]["demo-skill"]["enabled"] = True
    (workspace_dir / "skill.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return workspace_dir


@pytest.mark.asyncio
async def test_no_input_without_optin_returns_details(
    tmp_path,
    monkeypatch,
) -> None:
    """Default: ``/demo-skill`` with no input shows the details card."""
    from qwenpaw import constant

    monkeypatch.setattr(
        constant,
        "WORKING_DIR",
        tmp_path / "work",
        raising=True,
    )
    workspace_dir = _setup_workspace(
        tmp_path,
        _skill_md("demo-skill", "Demo description.", invoke_without_input=False),
    )
    ctx = _make_ctx(workspace_dir, "/demo-skill")

    result = await _skill_fallback_handler("/demo-skill", ctx)

    assert result is not None
    text = result.content[0].text
    assert "**demo-skill**" in text
    assert "to invoke" in text
    # No injection happened: the user message keeps its raw query.
    assert ctx.input_msgs[-1].content[0].text == "/demo-skill"


@pytest.mark.asyncio
async def test_no_input_with_optin_injects_and_invokes(
    tmp_path,
    monkeypatch,
) -> None:
    """Opt-in: ``/demo-skill`` with no input injects the body and invokes."""
    from qwenpaw import constant

    monkeypatch.setattr(
        constant,
        "WORKING_DIR",
        tmp_path / "work",
        raising=True,
    )
    workspace_dir = _setup_workspace(
        tmp_path,
        _skill_md("demo-skill", "Demo description.", invoke_without_input=True),
    )
    ctx = _make_ctx(workspace_dir, "/demo-skill")

    result = await _skill_fallback_handler("/demo-skill", ctx)

    assert result is None
    injected = ctx.input_msgs[-1].content[0].text
    assert "<skill>" in injected
    assert "Run the default workflow when invoked." in injected


@pytest.mark.asyncio
async def test_with_input_still_invokes_even_without_optin(
    tmp_path,
    monkeypatch,
) -> None:
    """Input present always invokes, opt-in or not."""
    from qwenpaw import constant

    monkeypatch.setattr(
        constant,
        "WORKING_DIR",
        tmp_path / "work",
        raising=True,
    )
    workspace_dir = _setup_workspace(
        tmp_path,
        _skill_md("demo-skill", "Demo description.", invoke_without_input=False),
    )
    ctx = _make_ctx(workspace_dir, "/demo-skill focus on macOS")

    result = await _skill_fallback_handler(
        "/demo-skill focus on macOS",
        ctx,
    )

    assert result is None
    injected = ctx.input_msgs[-1].content[0].text
    assert "<skill>" in injected
    assert "focus on macOS" in injected


@pytest.mark.asyncio
async def test_quoted_false_optin_is_not_truthy(
    tmp_path,
    monkeypatch,
) -> None:
    """``invoke_without_input: "false"`` must not opt the skill in."""
    from qwenpaw import constant

    monkeypatch.setattr(
        constant,
        "WORKING_DIR",
        tmp_path / "work",
        raising=True,
    )
    skill_md = _skill_md(
        "demo-skill",
        "Demo description.",
        invoke_without_input=False,
    ).replace("---\n\n", 'invoke_without_input: "false"\n---\n\n', 1)
    workspace_dir = _setup_workspace(tmp_path, skill_md)
    ctx = _make_ctx(workspace_dir, "/demo-skill")

    result = await _skill_fallback_handler("/demo-skill", ctx)

    assert result is not None
    assert "**demo-skill**" in result.content[0].text
