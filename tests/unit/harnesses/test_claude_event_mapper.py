# -*- coding: utf-8 -*-
"""Tests for translating Claude Code SDK messages."""

from __future__ import annotations

import pytest

# pylint: disable=wrong-import-position
# flake8: noqa: E402,E501
pytest.importorskip(
    "claude_agent_sdk",
    reason="optional claude_agent_sdk (claude extra) is required",
)

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SessionMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from qwenpaw.harnesses.events import (
    HarnessEventKind,
    HarnessHistoryKind,
)
from qwenpaw.harnesses.claude.event_mapper import (
    ClaudeEventMapper,
    history_items,
)

_MODEL = "claude-opus-5"


def _stream(delta: dict) -> StreamEvent:
    return StreamEvent(
        uuid="stream-1",
        session_id="session-1",
        event={"type": "content_block_delta", "delta": delta},
    )


def _result(**overrides) -> ResultMessage:
    payload = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session-1",
    }
    payload.update(overrides)
    return ResultMessage(**payload)


def test_text_delta_streams_incrementally() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(_stream({"type": "text_delta", "text": "he"}))

    assert [(event.kind, event.text) for event in events] == [
        (HarnessEventKind.TEXT_DELTA, "he"),
    ]


def test_thinking_delta_maps_to_reasoning() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        _stream({"type": "thinking_delta", "thinking": "hmm"}),
    )

    assert events[0].kind == HarnessEventKind.REASONING_DELTA
    assert events[0].text == "hmm"


def test_unrelated_stream_events_are_ignored() -> None:
    mapper = ClaudeEventMapper()

    assert mapper.convert(_stream({"type": "signature_delta"})) == []
    assert (
        mapper.convert(
            StreamEvent(
                uuid="u",
                session_id="s",
                event={"type": "message_start"},
            ),
        )
        == []
    )


def test_assistant_text_is_skipped_after_streaming() -> None:
    mapper = ClaudeEventMapper()
    mapper.convert(_stream({"type": "text_delta", "text": "hello"}))

    events = mapper.convert(
        AssistantMessage(content=[TextBlock(text="hello")], model=_MODEL),
    )

    assert events == []


def test_assistant_text_is_emitted_without_streaming() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        AssistantMessage(content=[TextBlock(text="hello")], model=_MODEL),
    )

    assert [(event.kind, event.text) for event in events] == [
        (HarnessEventKind.TEXT_DELTA, "hello"),
    ]


def test_assistant_thinking_is_emitted_without_streaming() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        AssistantMessage(
            content=[ThinkingBlock(thinking="plan", signature="sig")],
            model=_MODEL,
        ),
    )

    assert events[0].kind == HarnessEventKind.REASONING_DELTA
    assert events[0].text == "plan"


def test_tool_use_and_result_pair_up_by_id() -> None:
    mapper = ClaudeEventMapper()

    started = mapper.convert(
        AssistantMessage(
            content=[
                ToolUseBlock(id="call-1", name="Bash", input={"command": "ls"}),
            ],
            model=_MODEL,
        ),
    )
    completed = mapper.convert(
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id="call-1", content="a\nb"),
            ],
        ),
    )

    assert started[0].kind == HarnessEventKind.TOOL_STARTED
    assert started[0].item_id == "call-1"
    assert started[0].tool_name == "Bash"
    assert started[0].data == {"arguments": {"command": "ls"}}
    assert completed[0].kind == HarnessEventKind.TOOL_COMPLETED
    assert completed[0].tool_name == "Bash"
    assert completed[0].text == "a\nb"
    assert completed[0].data == {"is_error": False}


def test_denied_tool_result_reports_an_error() -> None:
    mapper = ClaudeEventMapper()
    mapper.convert(
        AssistantMessage(
            content=[ToolUseBlock(id="call-1", name="Bash", input={})],
            model=_MODEL,
        ),
    )

    events = mapper.convert(
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="call-1",
                    content="Rejected by the user.",
                    is_error=True,
                ),
            ],
        ),
    )

    assert events[0].kind == HarnessEventKind.TOOL_COMPLETED
    assert events[0].data == {"is_error": True}


def test_unknown_tool_result_falls_back_to_a_generic_name() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        UserMessage(content=[ToolResultBlock(tool_use_id="orphan")]),
    )

    assert events[0].tool_name == "tool"
    assert events[0].text == ""


def test_string_user_content_produces_no_events() -> None:
    mapper = ClaudeEventMapper()

    assert mapper.convert(UserMessage(content="hello")) == []


def test_assistant_error_is_reported() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        AssistantMessage(content=[], model=_MODEL, error="overloaded"),
    )

    assert events[0].kind == HarnessEventKind.ERROR
    assert events[0].text == "overloaded"


def test_result_errors_take_priority_over_the_result_text() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        _result(is_error=True, result="ignored", errors=["boom", "again"]),
    )

    assert events[0].kind == HarnessEventKind.ERROR
    assert events[0].text == "boom\nagain"


def test_errorless_failure_falls_back_to_the_subtype() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(_result(subtype="error_max_turns", is_error=True))

    assert events[0].kind == HarnessEventKind.ERROR
    assert events[0].text == "error_max_turns"


@pytest.mark.parametrize(
    "overrides",
    [
        {"subtype": "interrupted"},
        {"subtype": "cancelled"},
        {"stop_reason": "interrupt"},
        {"stop_reason": "aborted_tools"},
    ],
)
def test_interrupted_turns_map_to_cancelled(overrides: dict) -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(_result(**overrides))

    assert [event.kind for event in events] == [HarnessEventKind.CANCELLED]


def test_unstreamed_result_text_is_emitted_before_completion() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(_result(result="final answer"))

    assert [(event.kind, event.text) for event in events] == [
        (HarnessEventKind.TEXT_DELTA, "final answer"),
        (HarnessEventKind.COMPLETED, ""),
    ]


def test_streamed_turns_complete_without_repeating_text() -> None:
    mapper = ClaudeEventMapper()
    mapper.convert(_stream({"type": "text_delta", "text": "final answer"}))

    events = mapper.convert(_result(result="final answer"))

    assert [event.kind for event in events] == [HarnessEventKind.COMPLETED]


def test_local_command_output_is_surfaced_as_text() -> None:
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        SystemMessage(
            subtype="local_command_output",
            data={"content": "compacted"},
        ),
    )

    assert [(event.kind, event.text) for event in events] == [
        (HarnessEventKind.TEXT_DELTA, "compacted"),
    ]


def test_other_system_messages_are_ignored() -> None:
    mapper = ClaudeEventMapper()

    assert mapper.convert(SystemMessage(subtype="init", data={})) == []


def _session_message(message: dict, kind: str = "assistant") -> SessionMessage:
    return SessionMessage(
        type=kind,
        uuid="uuid-1",
        session_id="session-1",
        message=message,
    )


def test_history_maps_every_transcript_block() -> None:
    items = history_items(
        _session_message(
            {
                "content": [
                    {"type": "text", "text": "answer"},
                    {"type": "thinking", "thinking": "plan"},
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Read",
                        "input": {"path": "a.py"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "body",
                        "is_error": False,
                    },
                ],
            },
        ),
    )

    assert [item.kind for item in items] == [
        HarnessHistoryKind.MESSAGE,
        HarnessHistoryKind.REASONING,
        HarnessHistoryKind.TOOL_CALL,
        HarnessHistoryKind.TOOL_OUTPUT,
    ]
    assert items[2].tool_name == "Read"
    assert items[2].data == {"arguments": {"path": "a.py"}}
    assert items[3].text == "body"


def test_history_marks_user_text() -> None:
    items = history_items(
        _session_message({"content": [{"type": "text", "text": "hi"}]}, "user"),
    )

    assert items[0].kind == HarnessHistoryKind.USER


def test_history_accepts_plain_string_content() -> None:
    items = history_items(_session_message({"content": "hi"}, "user"))

    assert [(item.kind, item.text) for item in items] == [
        (HarnessHistoryKind.USER, "hi"),
    ]


def test_history_ignores_unparseable_messages() -> None:
    assert history_items(_session_message({})) == []
    assert (
        history_items(
            SessionMessage(
                type="user",
                uuid="u",
                session_id="s",
                message="not a dict",
            ),
        )
        == []
    )


def test_history_flattens_structured_tool_output() -> None:
    items = history_items(
        _session_message(
            {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": [
                            {"type": "text", "text": "line one"},
                            {"type": "text", "text": "line two"},
                        ],
                        "is_error": True,
                    },
                ],
            },
        ),
    )

    assert items[0].text == "line one\nline two"
    assert items[0].data == {"is_error": True}


def test_slash_command_output_is_not_repeated_by_the_result() -> None:
    # The CLI reports a slash command's output as local_command_output and
    # again as ResultMessage.result. Emitting both doubled the text.
    mapper = ClaudeEventMapper()

    streamed = mapper.convert(
        SystemMessage(
            subtype="local_command_output",
            data={"content": "Unknown command: /typo"},
        ),
    )
    finished = mapper.convert(_result(result="Unknown command: /typo"))

    assert [(event.kind, event.text) for event in streamed] == [
        (HarnessEventKind.TEXT_DELTA, "Unknown command: /typo"),
    ]
    assert [event.kind for event in finished] == [HarnessEventKind.COMPLETED]


def test_empty_local_command_output_still_lets_the_result_speak() -> None:
    mapper = ClaudeEventMapper()

    mapper.convert(SystemMessage(subtype="local_command_output", data={}))
    finished = mapper.convert(_result(result="fallback text"))

    assert [(event.kind, event.text) for event in finished] == [
        (HarnessEventKind.TEXT_DELTA, "fallback text"),
        (HarnessEventKind.COMPLETED, ""),
    ]


def test_non_streaming_assistant_text_is_not_repeated_by_the_result() -> None:
    # Slash-command replies arrive as one AssistantMessage plus a
    # ResultMessage carrying the same text, with no stream deltas at all.
    mapper = ClaudeEventMapper()

    spoken = mapper.convert(
        AssistantMessage(
            content=[TextBlock(text="Unknown command: /typo")],
            model=_MODEL,
        ),
    )
    finished = mapper.convert(_result(result="Unknown command: /typo"))

    assert [(event.kind, event.text) for event in spoken] == [
        (HarnessEventKind.TEXT_DELTA, "Unknown command: /typo"),
    ]
    assert [event.kind for event in finished] == [HarnessEventKind.COMPLETED]


def test_every_text_block_of_one_message_is_emitted() -> None:
    # The streamed flag is decided per message, so a second block must not be
    # swallowed by the first one setting it.
    mapper = ClaudeEventMapper()

    events = mapper.convert(
        AssistantMessage(
            content=[TextBlock(text="one"), TextBlock(text="two")],
            model=_MODEL,
        ),
    )

    assert [event.text for event in events] == ["one", "two"]


def test_empty_assistant_text_leaves_the_result_free_to_speak() -> None:
    mapper = ClaudeEventMapper()

    mapper.convert(
        AssistantMessage(content=[TextBlock(text="")], model=_MODEL),
    )
    finished = mapper.convert(_result(result="fallback"))

    assert [(event.kind, event.text) for event in finished] == [
        (HarnessEventKind.TEXT_DELTA, "fallback"),
        (HarnessEventKind.COMPLETED, ""),
    ]
