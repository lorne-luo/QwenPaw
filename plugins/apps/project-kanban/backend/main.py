# -*- coding: utf-8 -*-
"""Project Kanban — PawApp backend entry.

A per-project, multi-agent kanban: projects own a brief, a member list of
agents and issues; each issue runs through its assignee via
``ctx.chat_stream`` with one running issue per agent across all projects.

PluginLoader executes this file as the package ``plugin_project_kanban``
whose ``__path__`` contains this directory, so relative imports resolve
``project_kanban_core`` (requirements §16, Q3). Data lives in
``<app_dir>/data/board.json``.
"""

from pathlib import Path
from typing import Any, Optional

from qwenpaw.pawapp import PawApp, PawAppContext

from .project_kanban_core import get_logger
from .project_kanban_core.agents import AgentDirectory
from .project_kanban_core.dispatcher import Dispatcher
from .project_kanban_core.routes import build_router
from .project_kanban_core.runner import RUN_CHANNEL, RUN_USER_ID, Runner
from .project_kanban_core.store import Store
from .project_kanban_core.tools import build_tools

logger = get_logger(__name__)

APP_ID = "project-kanban"
APP_DIR = Path(__file__).resolve().parent.parent

store = Store(APP_DIR / "data")
agents = AgentDirectory()
runner = Runner(store, agents)
dispatcher = Dispatcher(store, runner, agents)

app = PawApp(name="Project Kanban", app_id=APP_ID)
app.include_router(build_router(store, dispatcher, agents))

for _spec in build_tools(store):
    # Local, read-only board queries: "internal" in the governance
    # taxonomy (Q7), like the host's local memory_search tool.
    app.tool(
        _spec["name"],
        description=_spec["description"],
        icon=_spec["icon"],
        tool_type="internal",
    )(_spec["func"])


def _launch_ctx() -> Optional[Any]:
    """Build a ctx from the host app state at launch (Q1).

    The plugin registry keeps the FastAPI app, whose state already holds
    the workspace registry and app services when startup hooks run.
    """
    api = getattr(app, "_plugin_api", None)
    registry = getattr(api, "_registry", None)
    http_app = registry.get_plugin_http_app() if registry is not None else None
    state = getattr(http_app, "state", None)
    manager = getattr(state, "multi_agent_manager", None)
    if manager is None:
        return None
    return PawAppContext(
        app_id=APP_ID,
        agent_id="default",
        channel=RUN_CHANNEL,
        user_id=RUN_USER_ID,
        _workspace_registry=manager,
        _app_services=getattr(state, "app_services", None),
        _plugin_registry=getattr(state, "plugin_registry", None),
    )


@app.on_launch
async def on_launch() -> None:
    try:
        ctx = _launch_ctx()
    except Exception:  # noqa: BLE001 - fall back to the first request
        logger.warning(
            "[project-kanban] launch ctx unavailable", exc_info=True
        )
        ctx = None
    await dispatcher.start(ctx)


@app.on_terminate
async def on_terminate() -> None:
    await dispatcher.stop()


# The 'plugin' variable is what PluginLoader looks for.
plugin = app
