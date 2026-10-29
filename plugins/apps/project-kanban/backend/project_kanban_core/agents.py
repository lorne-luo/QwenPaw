# -*- coding: utf-8 -*-
"""Host agent lookup (requirements R-MEM-2, Q6).

Agent ids come from the host config (``config.agents.profiles``), the same
source as the host's ``GET /agents``. When the host config cannot be read,
``exists`` returns ``None`` ("unknown") and callers accept the id.
"""

from typing import Optional, Set

from . import get_logger

logger = get_logger(__name__)


class AgentDirectory:
    """Resolve agent ids and display names from the host config."""

    def known_ids(self) -> Optional[Set[str]]:
        try:
            from qwenpaw.config.utils import load_config

            return set(load_config().agents.profiles.keys())
        except Exception:  # noqa: BLE001 - host API unavailable
            logger.debug(
                "[project-kanban] agent list unavailable", exc_info=True
            )
            return None

    def exists(self, agent_id: str) -> Optional[bool]:
        """``True``/``False`` when known; ``None`` if it cannot be checked."""
        ids = self.known_ids()
        if ids is None:
            return None
        return agent_id in ids

    def display_name(self, agent_id: str) -> str:
        try:
            from qwenpaw.config.config import load_agent_config

            return load_agent_config(agent_id).name or agent_id
        except Exception:  # noqa: BLE001 - unknown agent or host API change
            return agent_id
