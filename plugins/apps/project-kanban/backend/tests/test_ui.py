# -*- coding: utf-8 -*-
"""UI module checks (no browser): runs ui/index.js in a Node sandbox.

Covers what can be verified without rendering: route registration,
zh/en key parity (R-I18N-2), error-code messages for every backend code
(R-UI-9), hardened markdown (R-SEC-2 / D8), the client-side transition
check (R-SM-1) and key derivation parity with the backend.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from project_kanban_core import rules
from project_kanban_core.errors import CATALOG

HARNESS = Path(__file__).with_name("ui_harness.js")
NODE = shutil.which("node")

MARKDOWN = {
    "js_link": "[click](javascript:alert(1))",
    "data_link": "[x](data:text/html,<script>alert(1)</script>)",
    "quote_attr": '[x](https://ok.example/" onmouseover="alert(1))',
    "raw_html": '<img src=x onerror="alert(1)">',
    "single_quote": "it's <b>",
    "http_link": "[docs](https://example.com/a_b_c?x=1&y=2)",
    "code": "`<script>` and ```\n<b>block</b>\n```",
    "bold": "**strong** and *em*",
    "snake": "call list_project_issues now",
    "slot_in_url": "[x](https://a`b`c)",
}

DERIVE = [
    ["Website rebuild", []],
    ["Website rebuild", ["WR"]],
    ["Website", []],
    ["Q4 report for the board", []],
    ["数据迁移", ["P1"]],
    ["A", []],
]


@pytest.fixture(scope="module")
def ui():
    if NODE is None:
        pytest.skip("node is not installed")
    payload = json.dumps({"markdown": MARKDOWN, "derive": DERIVE})
    result = subprocess.run(
        [NODE, str(HARNESS), payload],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def test_route_registration(ui):
    assert ui["registered"] == [
        {
            "pluginId": "project-kanban",
            "paths": ["/apps/project-kanban"],
            "labels": ["Project Kanban"],
        },
    ]
    assert not [log for log in ui["logs"] if log[0] == "error"]


def test_i18n_key_sets_identical(ui):
    assert ui["zhKeys"] == ui["enKeys"]
    assert ui["missingKeys"] == []
    assert ui["locale"] == ["zh", "zh", "en", "en"]
    assert ui["fallback"] == "no.such.key"


def test_every_used_key_is_defined(ui):
    source = (HARNESS.parents[2] / "ui" / "index.js").read_text(
        encoding="utf-8"
    )
    used = set(re.findall(r'\bt\(\s*"([\w.]+)"', source))
    used |= set(re.findall(r'labelKey: "(\w+)"', source))
    used |= set(re.findall(r'warning: "(\w+)"', source))
    used |= {"approved", "denied", "toolCompleted", "toolStarted"}  # ternaries
    used.discard("err.")  # dynamic "err." + code, covered above
    assert len(used) > 100
    assert sorted(used - set(ui["enKeys"])) == []


def test_every_error_code_is_translated(ui):
    codes = {key[len("err.") :] for key in ui["errKeys"]}
    assert set(CATALOG) <= codes
    assert ui["genericError"] == "请求失败（HTTP 500）"
    assert ui["networkError"] == "Network error, please retry"
    assert ui["unknownCode"] == "Request failed (HTTP 418)"


def test_api_error_uses_detail_code(ui):
    err = ui["apiError"]
    assert err["status"] == 409 and err["code"] == "PROJECT_ARCHIVED"
    assert err["text"] == "The project is archived and cannot be changed"
    assert (
        err["url"] == "http://host/api/project-kanban/projects/p%2F1/archive"
    )
    assert err["auth"] == "Bearer tok"
    assert ui["streamUrl"] == (
        "http://host/api/project-kanban/projects/p/issues/i/stream?token=tok"
    )


def test_markdown_is_hardened(ui):
    md = ui["markdown"]
    for name in ("js_link", "data_link", "quote_attr"):
        assert "<a " not in md[name], name
    assert "javascript:alert(1)" in md["js_link"]  # shown as plain text
    assert "&lt;script&gt;" in md["data_link"]
    assert "onmouseover=&quot;" in md["quote_attr"]
    assert "<img" not in md["raw_html"] and "&lt;img" in md["raw_html"]
    assert "&quot;alert(1)&quot;" in md["raw_html"]
    assert md["single_quote"] == "it&#39;s &lt;b&gt;"
    assert md["http_link"].startswith(
        '<a href="https://example.com/a_b_c?x=1&amp;y=2" target="_blank" '
        'rel="noopener noreferrer"',
    )
    assert "<script>" not in md["code"] and "&lt;script&gt;" in md["code"]
    assert "&lt;b&gt;block&lt;/b&gt;" in md["code"]
    assert md["bold"] == "<strong>strong</strong> and <em>em</em>"
    assert md["snake"] == "call list_project_issues now"
    # A code slot inside a URL must not leak markup into an href.
    assert "<a " not in md["slot_in_url"] and "href" not in md["slot_in_url"]


def test_client_move_check_matches_backend_table(ui):
    for move, result in ui["moves"].items():
        source, target = move.split(">")
        if source == target:
            assert result == {"action": "none"}
        elif source == "in_progress":
            assert result["warning"] == "issueRunningLocal"
        elif target == "in_progress":
            expected = "run" if source in rules.RUN_FROM else None
            assert result.get("action") == expected, move
        elif (source, target) in rules.PATCH_TRANSITIONS:
            assert result == {"action": "patch"}, move
        else:
            assert result["warning"] == "invalidMove", move


def test_key_derivation_matches_backend(ui):
    for name, taken in DERIVE:
        assert ui["derived"][name + "|" + ",".join(taken)] == rules.derive_key(
            name,
            taken,
        )


def test_live_stream_reconnects_while_running(ui):
    """P1: a dropped SSE connection for a running issue is reopened and the
    text is rebuilt from the replay instead of being appended twice."""
    r = ui["streams"]["reconnect"]
    assert r["beforeDrop"] == "a\ntool_start:grep\n"
    assert r["firstClosed"] is True  # no native auto-reconnect duplicates
    assert r["delay"] == 1000 and r["sources"] == 2
    assert r["keptDuringReconnect"] == r["beforeDrop"]  # no flicker
    assert r["after"] == "a\ntool_start:grep\nb\n"  # no duplicate text
    assert r["finished"] == []
    assert r["finishedAfterDone"] == ["i1"] and r["textAfterDone"] is None


def test_live_stream_closed_after_run_finishes(ui):
    ended = ui["streams"]["ended"]
    assert ended == {"finished": ["i2"], "timers": 0, "text": None}


def test_live_stream_offline_backoff_and_collapse(ui):
    offline = ui["streams"]["offline"]
    assert offline["delays"] == [1000, 2000]
    assert offline["cancelled"] is True  # collapsing stops reconnecting
    assert offline["sources"] == 2 and offline["lastClosed"] is True
    assert offline["text"] is None and offline["finished"] == []
    assert ui["streams"]["backoff"] == [1000, 2000, 4000, 8000, 15000, 15000]
    assert ui["streams"]["closeAll"] == {"closed": True, "text": None}
