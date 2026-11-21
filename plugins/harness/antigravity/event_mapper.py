# -*- coding: utf-8 -*-
"""Normalize one Antigravity turn without inventing token deltas."""

from qwenpaw.plugins.harnesses import HarnessEvent
from qwenpaw.plugins.harnesses import HarnessEventKind as Kind


class EventMapper:
    def __init__(self, session_id, turn_id):
        self.prefix = f"{session_id}:{turn_id}"
        self.emitted_text = False
        self.started = set()

    def map(self, raw):
        event = raw.get("event")
        if event == "result":
            raw = raw.get("result", raw)
            result = []
            response = raw.get("response")
            if response and not self.emitted_text:
                result.append(
                    HarnessEvent(Kind.TEXT_DELTA, text=str(response)),
                )
            status = str(raw.get("status", "")).upper()
            kind = (
                Kind.COMPLETED
                if status == "SUCCESS"
                else (
                    Kind.CANCELLED
                    if status in {"CANCELED", "INTERRUPTED"}
                    else Kind.ERROR
                )
            )
            result.append(
                HarnessEvent(
                    kind,
                    text=str(raw.get("error", "")),
                    data={
                        "provider_result": raw,
                        "usage_scope": "provider_cumulative_raw",
                    },
                ),
            )
            return result
        if event not in {"step", "step_update"}:
            return []
        step = raw.get("step_update", raw.get("step", raw))
        kind = step.get("step_type", step.get("type", ""))
        if kind in {"TEXT_DELTA", "agent_response"}:
            text = step.get(
                "text_delta",
                step.get("text", step.get("delta", "")),
            )
            if text:
                self.emitted_text = True
                return [HarnessEvent(Kind.TEXT_DELTA, text=str(text))]
            return []
        if kind in {"TOOL_CALL", "TOOL", "tool"}:
            index = step.get(
                "step_index",
                step.get("id", step.get("step_id", "")),
            )
            item_id = f"{self.prefix}:{index}"
            result = []
            info = step.get("tool_info", {})
            data = {
                "arguments": info.get("parameters", {}),
                "provider_step": step,
            }
            name = step.get("name", step.get("tool_name", "tool"))
            if item_id not in self.started:
                self.started.add(item_id)
                result.append(
                    HarnessEvent(
                        Kind.TOOL_STARTED,
                        item_id=item_id,
                        tool_name=name,
                        data=data,
                    ),
                )
            is_done = step.get("state", step.get("status")) == "DONE"
            result.append(
                HarnessEvent(
                    Kind.TOOL_COMPLETED if is_done else Kind.TOOL_PROGRESS,
                    item_id=item_id,
                    tool_name=name,
                    data=data,
                    text=str(info.get("output", "")),
                ),
            )
            return result
        return []
