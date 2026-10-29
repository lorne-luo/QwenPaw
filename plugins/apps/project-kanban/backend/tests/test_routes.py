# -*- coding: utf-8 -*-
"""HTTP layer: error envelope, status codes, end-to-end flows."""

import importlib.util
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeAgents, Hub, wait_until
from project_kanban_core.dispatcher import Dispatcher
from project_kanban_core.errors import CATALOG
from project_kanban_core.routes import build_router
from project_kanban_core.runner import Runner
from project_kanban_core.store import Store
from qwenpaw.pawapp import get_ctx

SPEC_CODES = {
    "VALIDATION_ERROR": 400,
    "PROJECT_NOT_FOUND": 404,
    "ISSUE_NOT_FOUND": 404,
    "MEMBER_NOT_FOUND": 404,
    "APPROVAL_NOT_FOUND": 404,
    "AGENT_NOT_FOUND": 400,
    "PROJECT_KEY_TAKEN": 409,
    "PROJECT_ARCHIVED": 409,
    "PROJECT_NOT_ARCHIVED": 409,
    "PROJECT_HAS_RUNNING": 409,
    "MEMBER_BUSY": 409,
    "ASSIGNEE_REQUIRED": 400,
    "ASSIGNEE_NOT_MEMBER": 400,
    "ASSIGNEE_LOCKED": 409,
    "INVALID_TRANSITION": 409,
    "ISSUE_RUNNING": 409,
    "APPROVAL_UNAVAILABLE": 501,
    "BOARD_UNAVAILABLE": 503,
}


def test_error_catalog_matches_spec():
    assert CATALOG == SPEC_CODES


@pytest.fixture
def client(tmp_path):
    hub = Hub()
    store = Store(tmp_path / "data")
    agents = FakeAgents(["alice", "bob"])
    runner = Runner(store, agents)
    dispatcher = Dispatcher(store, runner, agents, approval_api=lambda: None)
    app = FastAPI()
    app.include_router(build_router(store, dispatcher, agents, lambda: None))
    app.dependency_overrides[get_ctx] = hub.ctx
    with TestClient(app) as test_client:
        test_client.hub = hub
        test_client.store = store
        test_client.dispatcher = dispatcher
        yield test_client
    runner.cancel_all()


def _detail(response):
    return response.json()["detail"]


def test_project_crud_and_errors(client):
    r = client.post(
        "/projects", json={"name": "Website rebuild", "members": ["alice"]}
    )
    assert r.status_code == 201
    project = r.json()
    assert project["key"] == "WR" and project["counts"]["backlog"] == 0
    pid = project["id"]

    r = client.post("/projects", json={"name": "Other", "key": "WR"})
    assert r.status_code == 409
    assert _detail(r) == {
        "code": "PROJECT_KEY_TAKEN",
        "message": "key WR is taken",
    }
    r = client.post("/projects", json={"name": ""})
    assert r.status_code == 400 and _detail(r)["code"] == "VALIDATION_ERROR"
    assert "name" in _detail(r)["message"]
    r = client.post("/projects", json={"name": "X", "members": ["ghost"]})
    assert r.status_code == 400 and _detail(r)["code"] == "AGENT_NOT_FOUND"
    r = client.post("/projects", json={"members": "nope"})
    assert r.status_code == 422  # schema errors stay FastAPI's default

    assert client.get(f"/projects/{pid}").json()["name"] == "Website rebuild"
    assert client.get("/projects/missing").status_code == 404
    r = client.patch(f"/projects/{pid}", json={"description": "brief"})
    assert r.json()["description"] == "brief"
    assert [p["id"] for p in client.get("/projects").json()["projects"]] == [
        pid
    ]
    assert client.get("/projects?archived=bogus").status_code == 400

    r = client.delete(f"/projects/{pid}")
    assert (
        r.status_code == 409 and _detail(r)["code"] == "PROJECT_NOT_ARCHIVED"
    )
    assert client.post(f"/projects/{pid}/archive").json()["archived"] is True
    assert client.get("/projects").json()["projects"] == []
    assert len(client.get("/projects?archived=true").json()["projects"]) == 1
    assert len(client.get("/projects?archived=all").json()["projects"]) == 1
    r = client.post(f"/projects/{pid}/issues", json={"title": "x"})
    assert r.status_code == 409 and _detail(r)["code"] == "PROJECT_ARCHIVED"
    assert (
        client.post(f"/projects/{pid}/unarchive").json()["archived"] is False
    )
    client.post(f"/projects/{pid}/archive")
    assert client.delete(f"/projects/{pid}").json() == {
        "ok": True,
        "deleted_issues": 0,
    }


def test_members_and_issue_flow(client):
    pid = client.post(
        "/projects",
        json={"name": "Web", "key": "WEB", "members": ["alice"]},
    ).json()["id"]
    r = client.post(
        f"/projects/{pid}/members", json={"agent_ids": ["bob", "alice"]}
    )
    assert [m["agent_id"] for m in r.json()["members"]] == ["alice", "bob"]

    r = client.post(
        f"/projects/{pid}/issues",
        json={
            "title": "Header",
            "assignee": "bob",
            "status": "todo",
            "language": "zh",
        },
    )
    assert r.status_code == 201
    issue = r.json()
    assert issue["key"] == "WEB-1" and issue["status"] == "todo"
    r = client.post(
        f"/projects/{pid}/issues", json={"title": "x", "assignee": "eve"}
    )
    assert _detail(r)["code"] == "ASSIGNEE_NOT_MEMBER"
    r = client.post(
        f"/projects/{pid}/issues", json={"title": "x", "status": "todo"}
    )
    assert _detail(r)["code"] == "ASSIGNEE_REQUIRED"

    r = client.patch(
        f"/projects/{pid}/issues/{issue['id']}",
        json={"status": "review"},
    )
    assert r.status_code == 409 and _detail(r)["code"] == "INVALID_TRANSITION"
    r = client.patch(
        f"/projects/{pid}/issues/{issue['id']}",
        json={"status": "in_progress"},
    )
    assert _detail(r)["code"] == "INVALID_TRANSITION"

    r = client.delete(f"/projects/{pid}/members/bob")
    body = r.json()
    assert (body["unassigned"], body["dequeued"]) == (0, 1)
    listed = client.get(f"/projects/{pid}/issues").json()["issues"]
    assert listed[0]["status"] == "backlog" and listed[0]["assignee"] == ""
    assert (
        client.get(f"/projects/{pid}/issues?status=todo").json()["issues"]
        == []
    )
    assert client.get(f"/projects/{pid}/issues?status=x").status_code == 400
    r = client.delete(f"/projects/{pid}/members/bob")
    assert _detail(r)["code"] == "MEMBER_NOT_FOUND"
    assert client.delete(f"/projects/{pid}/issues/{issue['id']}").json() == {
        "ok": True
    }
    r = client.delete(f"/projects/{pid}/issues/{issue['id']}")
    assert _detail(r)["code"] == "ISSUE_NOT_FOUND"


def test_run_without_assignee_and_dispatch(client):
    pid = client.post("/projects", json={"name": "Web", "key": "WEB"}).json()[
        "id"
    ]
    iid = client.post(f"/projects/{pid}/issues", json={"title": "t"}).json()[
        "id"
    ]
    r = client.post(f"/projects/{pid}/issues/{iid}/run?language=en")
    assert r.status_code == 400 and _detail(r)["code"] == "ASSIGNEE_REQUIRED"
    assert client.get("/dispatch").json() == {"agents": {}}
    r = client.post(f"/projects/{pid}/approvals/r1/approve")
    assert (
        r.status_code == 501 and _detail(r)["code"] == "APPROVAL_UNAVAILABLE"
    )
    assert client.get(f"/projects/{pid}/approvals").json() == {"approvals": {}}
    assert client.get("/approvals/summary").json() == {"projects": {}}
    # Stopping an idle issue is a no-op returning the issue.
    assert client.post(f"/projects/{pid}/issues/{iid}/stop").json()[
        "status"
    ] == ("backlog")
    assert client.get(f"/projects/{pid}/issues/{iid}/result").json() == {
        "messages": [],
    }
    r = client.get(f"/projects/{pid}/issues/{iid}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"
    assert r.text == ""  # idle: replay (empty) then end
    assert (
        client.get(f"/projects/{pid}/issues/missing/stream").status_code == 404
    )


def test_run_end_to_end(client):
    pid = client.post(
        "/projects",
        json={"name": "Web", "key": "WEB", "members": ["alice"]},
    ).json()["id"]
    iid = client.post(
        f"/projects/{pid}/issues",
        json={"title": "t", "assignee": "alice"},
    ).json()["id"]
    r = client.post(f"/projects/{pid}/issues/{iid}/run?language=zh")
    body = r.json()
    assert body["started"] is True and body["queue_position"] is None
    deadline = time.time() + 3
    while time.time() < deadline:
        issue = client.get(f"/projects/{pid}/issues").json()["issues"][0]
        if issue["status"] == "review":
            break
        time.sleep(0.02)
    assert issue["status"] == "review"
    assert issue["last_run"]["result_excerpt"] == "done by alice"
    r = client.patch(f"/projects/{pid}/issues/{iid}", json={"status": "done"})
    assert r.json()["status"] == "done"
    r = client.post(f"/projects/{pid}/issues/{iid}/run")
    assert _detail(r)["code"] == "INVALID_TRANSITION"


def test_board_unavailable_is_503(client):
    client.store.data_dir.mkdir(parents=True, exist_ok=True)
    client.store.path.write_text(
        '{"schema_version": 99, "projects": [], "issues": []}'
    )
    r = client.get("/projects")
    assert r.status_code == 503 and _detail(r)["code"] == "BOARD_UNAVAILABLE"


def test_main_module_loads_like_plugin_loader():
    """Q3: load backend/main.py exactly as PluginLoader does."""
    backend = Path(__file__).resolve().parents[1]
    entry = backend / "main.py"
    name = "plugin_project_kanban_test"
    spec = importlib.util.spec_from_file_location(
        name,
        entry,
        submodule_search_locations=[str(backend), str(backend.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(backend), str(backend.parent)]
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        app = module.plugin
        assert app.app_id == "project-kanban"
        assert module.store.path == backend.parent / "data" / "board.json"
        tools = {t["name"]: t for t in app._tools}  # pylint: disable=W0212
        assert set(tools) == {
            "project_kanban_list_my_projects",
            "project_kanban_list_project_issues",
            "project_kanban_get_issue",
            "project_kanban_list_my_issues",
        }
        assert {t["tool_type"] for t in tools.values()} == {"internal"}
        paths = {
            route.path for router in app._routers for route in router.routes
        }
        assert "/projects/{project_id}/issues/{issue_id}/stream" in paths
        assert "/dispatch" in paths
        assert module._launch_ctx() is None  # not registered with a host
    finally:
        for key in [k for k in sys.modules if k.startswith(name)]:
            sys.modules.pop(key, None)


def test_permission_error_on_board_is_503_not_500(client, monkeypatch):
    import builtins

    client.post("/projects", json={"name": "Web", "key": "WEB"})
    store = client.store
    store._board = None  # force a reload  # pylint: disable=W0212
    monkeypatch.setattr("project_kanban_core.store._READ_RETRY_DELAY", 0)
    real_open = builtins.open

    def denied(path, *args, **kwargs):
        if str(path) == str(store.path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(
        "project_kanban_core.store.open", denied, raising=False
    )
    for method, path in (
        ("GET", "/projects"),
        ("GET", "/dispatch"),
        ("POST", "/projects"),
    ):
        response = client.request(method, path, json={"name": "X"})
        assert response.status_code == 503, (method, path)
        assert _detail(response)["code"] == "BOARD_UNAVAILABLE"
