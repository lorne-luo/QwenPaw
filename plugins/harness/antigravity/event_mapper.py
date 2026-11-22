# -*- coding: utf-8 -*-
"""Translate ACP ``session/update`` payloads into QwenPaw events."""

import difflib

from qwenpaw.plugins.harnesses import HarnessEvent, HarnessHistoryItem
from qwenpaw.plugins.harnesses import HarnessEventKind as Kind
from qwenpaw.plugins.harnesses import HarnessHistoryKind as History

_CHUNKS = {
    "agent_message_chunk": (Kind.TEXT_DELTA, History.MESSAGE),
    "agent_thought_chunk": (Kind.REASONING_DELTA, History.REASONING),
    "user_message_chunk": (None, History.USER),
}
_TOOL_UPDATES = {"tool_call", "tool_call_update"}
_TOOL_DONE = {"completed", "failed"}


def _text(content):
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text") or "")
    return ""


def _diff(item):
    path = str(item.get("path") or "")
    old = str(item.get("oldText") or "")
    new = str(item.get("newText") or "")
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            path,
            path,
        ),
    )


def tool_output(update):
    """Text and diff content of a tool call, else its raw output."""
    parts = []
    for item in update.get("content") or ():
        if not isinstance(item, dict):
            continue
        if item.get("type") == "content":
            parts.append(_text(item.get("content")))
        elif item.get("type") == "diff":
            parts.append(_diff(item))
    text = "\n".join(part for part in parts if part)
    raw = update.get("rawOutput")
    if not text and isinstance(raw, dict):
        text = raw.get("output") if isinstance(raw.get("output"), str) else ""
    return text


def _tool_fields(update):
    return (
        str(update.get("name") or update.get("kind") or "tool"),
        {
            "arguments": update.get("rawInput") or update.get("args") or {},
            "title": update.get("title"),
            "kind": update.get("kind"),
            "status": update.get("status"),
        },
    )


class EventMapper:
    """Map one turn; tool ids are scoped to the turn like before."""

    def __init__(self, session_id, turn_id):
        self.prefix = f"{session_id}:{turn_id}"
        self.context_window = None
        self._started = set()
        self._done = set()

    def map(self, update):
        kind = update.get("sessionUpdate")
        if kind in _CHUNKS and _CHUNKS[kind][0] is not None:
            text = _text(update.get("content"))
            return [HarnessEvent(_CHUNKS[kind][0], text=text)] if text else []
        if kind in _TOOL_UPDATES:
            return self._tool(update)
        if kind == "usage_update":
            self.context_window = {
                "used": update.get("used"),
                "size": update.get("size"),
            }
        return []

    def _tool(self, update):
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            return []
        item_id = f"{self.prefix}:{call_id}"
        if item_id in self._done:
            return []
        name, data = _tool_fields(update)
        events = []
        if item_id not in self._started:
            self._started.add(item_id)
            events.append(
                HarnessEvent(
                    Kind.TOOL_STARTED,
                    item_id=item_id,
                    tool_name=name,
                    data=data,
                ),
            )
        if update.get("status") in _TOOL_DONE:
            self._done.add(item_id)
            events.append(
                HarnessEvent(
                    Kind.TOOL_COMPLETED,
                    item_id=item_id,
                    tool_name=name,
                    data=data,
                    text=tool_output(update),
                ),
            )
        return events

    def finish(self, result):
        """The terminal event for a ``session/prompt`` response."""
        result = result if isinstance(result, dict) else {}
        stop = result.get("stopReason")
        data = {
            "stop_reason": stop,
            "usage": result.get("usage"),
            "usage_scope": "turn",
            "context_window": self.context_window,
        }
        if stop == "end_turn":
            return [HarnessEvent(Kind.COMPLETED, data=data)]
        if stop == "cancelled":
            return [HarnessEvent(Kind.CANCELLED, data=data)]
        return [
            HarnessEvent(
                Kind.ERROR,
                text=f"Antigravity stopped: {stop or 'unknown reason'}",
                data=data,
            ),
        ]


def history_items(updates):
    """Fold a ``session/load`` replay into history items."""
    rows = []
    seen = set()
    for update in updates:
        kind = update.get("sessionUpdate")
        if kind in _CHUNKS:
            text = _text(update.get("content"))
            history = _CHUNKS[kind][1]
            if not text:
                continue
            if rows and rows[-1]["kind"] == history:
                rows[-1]["text"] += text
            else:
                rows.append({"kind": history, "text": text})
            continue
        call_id = update.get("toolCallId")
        if kind not in _TOOL_UPDATES or not isinstance(call_id, str):
            continue
        name, data = _tool_fields(update)
        if (call_id, History.TOOL_CALL) not in seen:
            seen.add((call_id, History.TOOL_CALL))
            rows.append(
                {
                    "kind": History.TOOL_CALL,
                    "item_id": call_id,
                    "tool_name": name,
                    "data": {"arguments": data["arguments"]},
                },
            )
        if (
            update.get("status") in _TOOL_DONE
            and (call_id, History.TOOL_OUTPUT) not in seen
        ):
            seen.add((call_id, History.TOOL_OUTPUT))
            rows.append(
                {
                    "kind": History.TOOL_OUTPUT,
                    "item_id": call_id,
                    "tool_name": name,
                    "text": tool_output(update),
                },
            )
    return tuple(HarnessHistoryItem(**row) for row in rows)
