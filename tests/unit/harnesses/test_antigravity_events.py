# -*- coding: utf-8 -*-
import json
from pathlib import Path

from qwenpaw.harnesses.antigravity.event_mapper import EventMapper


def test_done_text_delta_is_emitted_without_result_duplication():
    mapper = EventMapper('chat', 'turn')
    events = mapper.map({'event': 'step_update', 'step_update': {
        'step_type': 'agent_response', 'state': 'DONE', 'text_delta': 'tail',
    }})
    assert [e.text for e in events] == ['tail']
    events = mapper.map({'event': 'result', 'result': {
        'status': 'SUCCESS', 'response': 'tail',
        'usage': {'total_tokens': 42},
    }})
    assert len(events) == 1
    assert events[0].kind == 'completed'
    assert events[0].data['usage_scope'] == 'provider_cumulative_raw'
    assert events[0].data['provider_result']['usage']['total_tokens'] == 42


def test_result_fallback_and_failure():
    mapper = EventMapper('chat', 'turn')
    events = mapper.map({'event': 'result', 'result': {
        'status': 'INTERRUPTED', 'response': 'partial',
    }})
    assert [e.kind for e in events] == ['text_delta', 'cancelled']


def test_tools_have_turn_scoped_ids_arguments_and_output():
    mapper = EventMapper('chat', 'turn')
    raw = {'event': 'step_update', 'step_update': {
        'step_index': 2, 'state': 'DONE', 'step_type': 'tool',
        'tool_name': 'run_command', 'tool_info': {
            'parameters': {'CommandLine': 'printf test'}, 'output': 'test',
        },
    }}
    events = mapper.map(raw)
    assert [e.kind for e in events] == ['tool_started', 'tool_completed']
    assert events[0].item_id == 'chat:turn:2'
    assert events[0].data['arguments'] == {'CommandLine': 'printf test'}
    assert events[1].text == 'test'


def test_replay_fixture_turn1():
    fixture_path = (
        Path(__file__).parents[2]
        / 'fixtures'
        / 'harnesses'
        / 'antigravity'
        / 'turn1.ndjson'
    )
    assert fixture_path.exists()
    mapper = EventMapper('chat', 'turn1')
    all_events = []
    for line in fixture_path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            all_events.extend(mapper.map(json.loads(line)))
    kinds = [e.kind.value for e in all_events]
    assert 'text_delta' in kinds
    assert kinds[-1] == 'completed'
