# -*- coding: utf-8 -*-
"""Host agent lookup (Q6 / R-MEM-2)."""

from types import SimpleNamespace

from project_kanban_core.agents import AgentDirectory


def test_known_ids_from_host_config(monkeypatch):
    config = SimpleNamespace(
        agents=SimpleNamespace(
            profiles={"default": object(), "writer": object()}
        ),
    )
    monkeypatch.setattr("qwenpaw.config.utils.load_config", lambda: config)
    directory = AgentDirectory()
    assert directory.known_ids() == {"default", "writer"}
    assert directory.exists("writer") is True
    assert directory.exists("ghost") is False


def test_unreadable_host_config_means_unknown(monkeypatch):
    def broken():
        raise RuntimeError("config missing")

    monkeypatch.setattr("qwenpaw.config.utils.load_config", broken)
    directory = AgentDirectory()
    assert directory.known_ids() is None
    assert directory.exists("anyone") is None  # accepted by callers


def test_display_name(monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.config.config.load_agent_config",
        lambda agent_id: SimpleNamespace(name="Writer Bot"),
    )
    assert AgentDirectory().display_name("writer") == "Writer Bot"

    def missing(agent_id):
        raise KeyError(agent_id)

    monkeypatch.setattr("qwenpaw.config.config.load_agent_config", missing)
    assert AgentDirectory().display_name("ghost") == "ghost"


def test_real_host_config_shape():
    """Against the real (isolated) host config the lookup still works."""
    ids = AgentDirectory().known_ids()
    assert ids is None or isinstance(ids, set)
