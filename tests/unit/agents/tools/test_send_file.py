# -*- coding: utf-8 -*-
"""Tests for the model-facing description of send_file_to_user."""

from qwenpaw.agents.tools import send_file_to_user
from qwenpaw.runtime.tool_guard import GuardedFunctionTool


def test_description_tells_model_media_is_previewed_inline():
    tool = GuardedFunctionTool(
        send_file_to_user,
        agent_id="test-agent",
        request_context={},
    )

    assert "plays audio and video in the chat" in tool.description
    assert "instead of" in tool.description
    assert "only replying with the path" in tool.description
