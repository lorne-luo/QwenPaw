# -*- coding: utf-8 -*-
"""Console streams that give up must still end the turn for the client."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.channels.console.channel import ConsoleChannel


@pytest.fixture
def stream_channel():
    return ConsoleChannel(
        process=AsyncMock(),
        enabled=True,
        bot_prefix=">> ",
    )


async def test_stream_one_emits_terminal_response_on_rate_limit(
    stream_channel,
):
    """A rate limit must still tell the client the turn is over.

    The frontend maps ``rate_limited`` to a no-op, so without a terminal
    ``response`` event the composer stays stuck in its loading state.
    """
    from qwenpaw.exceptions import ModelQuotaExceededException
    from qwenpaw.schemas import ContentType, TextContent

    async def mock_process(request):
        raise ModelQuotaExceededException("quota gone")
        yield  # pragma: no cover - makes this an async generator

    stream_channel._process = mock_process

    payload = {
        "sender_id": "user123",
        "content_parts": [TextContent(type=ContentType.TEXT, text="hi")],
        "meta": {},
    }

    events = [e async for e in stream_channel.stream_one(payload)]
    blob = "".join(events)

    assert '"type": "rate_limited"' in blob
    terminal = [e for e in events if '"object": "response"' in e]
    assert len(terminal) == 1
    assert '"status": "failed"' in terminal[0]
    assert "quota gone" in terminal[0]


async def test_stream_one_emits_terminal_response_on_error(stream_channel):
    """An unhandled failure used to close the stream silently."""
    from qwenpaw.schemas import ContentType, TextContent

    async def mock_process(request):
        raise RuntimeError("boom")
        yield  # pragma: no cover - makes this an async generator

    stream_channel._process = mock_process

    payload = {
        "sender_id": "user123",
        "content_parts": [TextContent(type=ContentType.TEXT, text="hi")],
        "meta": {},
    }

    events = [e async for e in stream_channel.stream_one(payload)]

    terminal = [e for e in events if '"object": "response"' in e]
    assert len(terminal) == 1
    assert '"status": "failed"' in terminal[0]
    assert "boom" in terminal[0]
