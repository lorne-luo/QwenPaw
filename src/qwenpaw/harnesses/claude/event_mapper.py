# -*- coding: utf-8 -*-
"""Translate Claude Code SDK messages into provider-neutral events."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ..events import (
    HarnessEvent,
    HarnessEventKind,
    HarnessHistoryItem,
    HarnessHistoryKind,
)

_CANCELLED_SUBTYPES = {"interrupted", "cancelled"}
_CANCELLED_STOP_REASONS = {"interrupt", "cancelled", "aborted_tools"}


class ClaudeEventMapper:
    """Keep per-turn de-duplication state while mapping SDK messages."""

    def __init__(self) -> None:
        self._streamed_text = False
        self._streamed_reasoning = False
        self._started_tools: dict[str, str] = {}

    def convert(self, message: Any) -> list[HarnessEvent]:
        """Convert one Claude Code SDK message."""
        if isinstance(message, StreamEvent):
            return self._stream_event(message.event)
        if isinstance(message, AssistantMessage):
            return self._assistant(message)
        if isinstance(message, UserMessage):
            return self._user(message)
        if isinstance(message, ResultMessage):
            return self._result(message)
        if isinstance(message, SystemMessage):
            return self._system(message)
        return []

    def _stream_event(self, event: dict[str, Any]) -> list[HarnessEvent]:
        if event.get("type") != "content_block_delta":
            return []
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return []
        delta_type = str(delta.get("type") or "")
        if delta_type == "text_delta":
            text = str(delta.get("text") or "")
            self._streamed_text = self._streamed_text or bool(text)
            return _text_event(HarnessEventKind.TEXT_DELTA, text)
        if delta_type == "thinking_delta":
            text = str(delta.get("thinking") or "")
            self._streamed_reasoning = self._streamed_reasoning or bool(text)
            return _text_event(HarnessEventKind.REASONING_DELTA, text)
        return []

    def _assistant(self, message: AssistantMessage) -> list[HarnessEvent]:
        events: list[HarnessEvent] = []
        # Decide once per message: setting the flags inside the loop would
        # make a second block in the same message look already-streamed.
        take_text = not self._streamed_text
        take_reasoning = not self._streamed_reasoning
        emitted_text = False
        emitted_reasoning = False
        for block in message.content:
            if isinstance(block, TextBlock) and take_text:
                new_events = _text_event(
                    HarnessEventKind.TEXT_DELTA,
                    block.text,
                )
                emitted_text = emitted_text or bool(new_events)
                events.extend(new_events)
            elif isinstance(block, ThinkingBlock) and take_reasoning:
                new_events = _text_event(
                    HarnessEventKind.REASONING_DELTA,
                    block.thinking,
                )
                emitted_reasoning = emitted_reasoning or bool(new_events)
                events.extend(new_events)
            elif isinstance(block, ToolUseBlock):
                self._started_tools[block.id] = block.name
                events.append(
                    HarnessEvent(
                        kind=HarnessEventKind.TOOL_STARTED,
                        item_id=block.id,
                        tool_name=block.name,
                        data={"arguments": block.input},
                    ),
                )
        # A non-streaming turn — a slash command reply, for instance — carries
        # its text here and again in ResultMessage.result. Recording it stops
        # the result from repeating the whole thing.
        self._streamed_text = self._streamed_text or emitted_text
        self._streamed_reasoning = self._streamed_reasoning or emitted_reasoning
        if message.error:
            events.append(
                HarnessEvent(
                    kind=HarnessEventKind.ERROR,
                    text=str(message.error),
                ),
            )
        return events

    def _user(self, message: UserMessage) -> list[HarnessEvent]:
        if not isinstance(message.content, list):
            return []
        events: list[HarnessEvent] = []
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            name = self._started_tools.pop(block.tool_use_id, "tool")
            events.append(
                HarnessEvent(
                    kind=HarnessEventKind.TOOL_COMPLETED,
                    item_id=block.tool_use_id,
                    tool_name=name,
                    text=_content_text(block.content),
                    data={"is_error": bool(block.is_error)},
                ),
            )
        return events

    def _result(self, message: ResultMessage) -> list[HarnessEvent]:
        if message.is_error:
            details = "\n".join(message.errors or [])
            text = details or message.result or message.subtype
            return [
                HarnessEvent(
                    kind=HarnessEventKind.ERROR,
                    text=text,
                ),
            ]
        if message.subtype in _CANCELLED_SUBTYPES or (
            message.stop_reason in _CANCELLED_STOP_REASONS
        ):
            return [HarnessEvent(kind=HarnessEventKind.CANCELLED)]
        if message.result and not self._streamed_text:
            return [
                HarnessEvent(
                    kind=HarnessEventKind.TEXT_DELTA,
                    text=message.result,
                ),
                HarnessEvent(kind=HarnessEventKind.COMPLETED),
            ]
        return [HarnessEvent(kind=HarnessEventKind.COMPLETED)]

    def _system(self, message: SystemMessage) -> list[HarnessEvent]:
        if message.subtype != "local_command_output":
            return []
        text = str(
            message.data.get("content") or message.data.get("output") or "",
        )
        # A slash command's output arrives here and again as
        # ResultMessage.result, so record it as streamed or the turn ends by
        # repeating the whole thing.
        self._streamed_text = self._streamed_text or bool(text)
        return _text_event(HarnessEventKind.TEXT_DELTA, text)


def history_items(message: Any) -> list[HarnessHistoryItem]:
    """Convert one Claude Code transcript message into recovery items."""
    raw = message.message
    if not isinstance(raw, dict):
        return []
    content = raw.get("content")
    blocks = content if isinstance(content, list) else []
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    history: list[HarnessHistoryItem] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        item_id = str(block.get("id") or block.get("tool_use_id") or "")
        if block_type == "text":
            kind = (
                HarnessHistoryKind.USER
                if message.type == "user"
                else HarnessHistoryKind.MESSAGE
            )
            history.append(
                HarnessHistoryItem(
                    kind=kind,
                    item_id=str(message.uuid or ""),
                    text=str(block.get("text") or ""),
                ),
            )
        elif block_type == "thinking":
            history.append(
                HarnessHistoryItem(
                    kind=HarnessHistoryKind.REASONING,
                    item_id=str(message.uuid or ""),
                    text=str(block.get("thinking") or ""),
                ),
            )
        elif block_type == "tool_use":
            history.append(
                HarnessHistoryItem(
                    kind=HarnessHistoryKind.TOOL_CALL,
                    item_id=item_id,
                    tool_name=str(block.get("name") or "tool"),
                    data={"arguments": block.get("input") or {}},
                ),
            )
        elif block_type == "tool_result":
            history.append(
                HarnessHistoryItem(
                    kind=HarnessHistoryKind.TOOL_OUTPUT,
                    item_id=item_id,
                    text=_content_text(block.get("content")),
                    data={"is_error": bool(block.get("is_error"))},
                ),
            )
    return history


def _text_event(
    kind: HarnessEventKind,
    text: str,
) -> list[HarnessEvent]:
    return [HarnessEvent(kind=kind, text=text)] if text else []


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value is not None:
                    text.append(str(value))
            else:
                text.append(str(item))
        return "\n".join(text)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


__all__ = ["ClaudeEventMapper", "history_items"]
