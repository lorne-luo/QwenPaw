# -*- coding: utf-8 -*-
"""Scripted stand-in for ``agy``: ``--version`` and an ``acp`` server.

State lives in ``$FAKE_ACP_STATE``: ``sessions.json`` (sessionId -> past
prompts, shared across processes for resume/load) and ``log.jsonl`` (every
message received, tagged with this pid). Prompt text selects a behaviour.
"""

import json
import os
import subprocess
import sys
import time

STATE = os.environ.get("FAKE_ACP_STATE", ".")
SESSIONS = os.path.join(STATE, "sessions.json")
LOG = os.path.join(STATE, "log.jsonl")
MODELS = {
    "availableModels": [
        {"modelId": "model-a-high", "name": "Model A (High)"},
        {"modelId": "model-a-low", "name": "Model A (Low)"},
    ],
    "currentModelId": "model-a-high",
}
CONFIG = [
    {
        "id": "thought_level",
        "type": "select",
        "currentValue": "high",
        "options": [{"value": "low"}, {"value": "high"}],
    },
]
MODES = {"currentModeId": "default", "availableModes": []}
GUARDED = {"session/new", "session/list", "session/resume"}
SETTERS = {
    "session/set_mode",
    "session/set_model",
    "session/set_config_option",
    "session/close",
}


def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(json.dumps({"pid": os.getpid(), **message}) + "\n")
    return message


def load():
    try:
        with open(SESSIONS, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def save(sessions):
    with open(SESSIONS, "w", encoding="utf-8") as handle:
        json.dump(sessions, handle)


def update(sid, body):
    send(
        {
            "method": "session/update",
            "params": {"sessionId": sid, "update": body},
        },
    )


def error(mid, code, text):
    send({"id": mid, "error": {"code": code, "message": text}})


def chunk(sid, text, kind="agent_message_chunk"):
    content = {"type": "text", "text": text}
    update(sid, {"sessionUpdate": kind, "content": content})


def session_result(sid):
    return {
        "sessionId": sid,
        "models": MODELS,
        "modes": MODES,
        "configOptions": CONFIG,
    }


def tool(sid):
    call = {
        "toolCallId": "call-1",
        "name": "run_command",
        "kind": "execute",
        "title": "echo hi",
        "rawInput": {"CommandLine": "echo hi"},
    }
    update(
        sid,
        {"sessionUpdate": "tool_call", "status": "in_progress", **call},
    )
    send(
        {
            "id": "perm-1",
            "method": "session/request_permission",
            "params": {
                "sessionId": sid,
                "toolCall": {"sessionUpdate": "tool_call", **call},
                "options": [
                    {"optionId": "allow", "kind": "allow_once"},
                    {"optionId": "deny", "kind": "reject_once"},
                ],
            },
        },
    )
    while True:
        message = read()
        if message.get("id") == "perm-1" and "method" not in message:
            break
    outcome = message["result"]["outcome"]
    chosen = outcome.get("optionId") or outcome["outcome"]
    status = "completed" if chosen == "allow" else "failed"
    update(
        sid,
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": status,
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": "hi\n"},
                },
            ]
            if status == "completed"
            else [],
        },
    )
    chunk(sid, f"outcome:{chosen}")


def prompt(message, sessions):
    params = message["params"]
    sid = params["sessionId"]
    if sid not in sessions:
        error(message["id"], -32602, f"Session not found: {sid}")
        return
    text = params["prompt"][0]["text"]
    sessions[sid].append(text)
    save(sessions)
    stop = "end_turn"
    if text == "tool":
        tool(sid)
    elif text == "slow":
        chunk(sid, "working")
        while read().get("method") != "session/cancel":
            pass
        stop = "cancelled"
    elif text == "ignore-cancel":
        chunk(sid, "stubborn")
        time.sleep(60)
    elif text == "garbage":
        sys.stdout.write("not json{\n[1, 2]\n")
        sys.stdout.flush()
        chunk(sid, "survived")
    elif text == "crash":
        sys.stderr.write("boom token=SECRET\n")
        sys.stderr.flush()
        sys.exit(3)
    elif text == "child":
        child = subprocess.Popen(["sleep", "300"])
        path = os.path.join(STATE, "child.pid")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
        chunk(sid, "spawned")
    elif text == "diff":
        update(
            sid,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "edit-1",
                "name": "edit_file",
                "status": "completed",
                "content": [
                    {
                        "type": "diff",
                        "path": "a.txt",
                        "oldText": "old\n",
                        "newText": "new\n",
                    },
                ],
            },
        )
    elif text == "refuse":
        stop = "refusal"
    else:
        chunk(sid, "thinking", "agent_thought_chunk")
        chunk(sid, f"pid:{os.getpid()} sid:{sid}")
        update(sid, {"sessionUpdate": "usage_update", "used": 7, "size": 100})
    send(
        {
            "id": message["id"],
            "result": {
                "stopReason": stop,
                "usage": {"inputTokens": 3, "outputTokens": 4},
            },
        },
    )


def serve():
    noauth = bool(os.environ.get("FAKE_ACP_NOAUTH"))
    while True:
        message = read()
        method, mid = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if mid is None:
            continue
        sessions = load()
        if noauth and method in GUARDED:
            error(mid, -32000, "Authentication required")
        elif method == "initialize":
            send(
                {
                    "id": mid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {
                            "loadSession": True,
                            "sessionCapabilities": {
                                "resume": {},
                                "close": {},
                            },
                        },
                    },
                },
            )
        elif method == "session/new":
            sid = f"session-{len(sessions) + 1}"
            sessions[sid] = []
            save(sessions)
            send({"id": mid, "result": session_result(sid)})
        elif method in {"session/resume", "session/load"}:
            sid = params["sessionId"]
            if sid not in sessions:
                error(mid, -32603, f"Failed to read session {sid}")
                continue
            if method == "session/load":
                for past in sessions[sid]:
                    chunk(sid, past, "user_message_chunk")
                    chunk(sid, f"reply to {past}")
            send({"id": mid, "result": session_result(sid)})
        elif method == "session/prompt":
            prompt(message, sessions)
        elif params.get("value") == "bogus":
            error(mid, -32602, 'Thinking level "bogus" is not offered')
        elif method in SETTERS:
            send({"id": mid, "result": {}})
        elif method == "session/list":
            send({"id": mid, "result": {"sessions": []}})
        else:
            error(mid, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--version"]:
        print(os.environ.get("FAKE_AGY_VERSION", "1.5.14"))
    elif sys.argv[1:2] == ["acp"] and not os.environ.get("FAKE_ACP_BROKEN"):
        serve()
    else:
        sys.exit(2)
