# -*- coding: utf-8 -*-
"""Model discovery keeps effort choices aligned with what agy accepts."""
import json
import os

import pytest

from qwenpaw.harnesses.antigravity.models import ModelDiscovery

MODELS = (
    'Fetching available models...\n'
    'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
    'gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n'
    'claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n'
)
EFFORT_ARGS = ['--output-format', 'json', '-p', '/effort']


def effort_reply(data):
    return json.dumps({
        'status': 'SUCCESS', 'command': {'name': 'effort', 'data': data},
    })


def cli(tmp_path, effort_body):
    binary = tmp_path / 'agy'
    binary.write_text(
        '#!/usr/bin/env python3\nimport sys\n'
        'if sys.argv[1:] == ["models"]:\n'
        f'    print({MODELS!r}, end=""); sys.exit()\n'
        f'assert sys.argv[1:] == {EFFORT_ARGS!r}\n'
        f'open({str(tmp_path / "effort-probed")!r}, "w").close()\n'
        + effort_body,
    )
    binary.chmod(0o755)
    return str(binary)


async def test_listed_variants_have_no_separate_effort(tmp_path):
    binary = cli(tmp_path, 'print({!r})\n'.format(effort_reply({
        'adjustable': True, 'current': 'high',
        'available': ['low', 'high', 'max'],
    })))
    default, *variants = await ModelDiscovery().models(
        binary, dict(os.environ), '1.2.11',
    )
    assert default.is_default
    assert default.reasoning_efforts == ['low', 'high', 'max']
    assert [model.id for model in variants] == [
        'gemini-3.8-flash-high', 'gemini-3.8-flash-low', 'claude-sonnet-4-6',
    ]
    assert all(model.reasoning_efforts == [] for model in variants)


@pytest.mark.parametrize('effort_body', [
    'print({!r})\n'.format(effort_reply({'adjustable': False})),
    'print({!r})\n'.format(effort_reply({
        'adjustable': True, 'available': ['low', 'turbo'],
    })),
    'print("not json")\n',
    'sys.exit(1)\n',
])
async def test_default_efforts_follow_cli_report(tmp_path, effort_body):
    binary = cli(tmp_path, effort_body)
    default = (await ModelDiscovery().models(
        binary, dict(os.environ), 'agy version 1.1.15',
    ))[0]
    expected = ['low'] if 'turbo' in effort_body else []
    assert default.reasoning_efforts == expected


@pytest.mark.parametrize('version', ['1.1.10', 'unknown', None])
async def test_old_cli_is_never_asked_for_effort(tmp_path, version):
    # Before 1.1.11, print mode sent "/effort" to the model as a prompt.
    binary = cli(tmp_path, 'print("{}")\n')
    default = (await ModelDiscovery().models(
        binary, dict(os.environ), version,
    ))[0]
    assert default.reasoning_efforts == []
    assert not (tmp_path / 'effort-probed').exists()
