# -*- coding: utf-8 -*-
"""Project Kanban backend package.

The package name is unique on purpose: plugin modules share one process,
so a generic name such as ``store`` could collide with another plugin.
"""

import logging

# The host prints INFO logs only for the ``qwenpaw`` logger namespace
# (``qwenpaw.utils.logging.setup_logger``), so plugin loggers live under it.
LOGGER_PREFIX = "qwenpaw.apps.project_kanban"


def get_logger(module_name: str) -> logging.Logger:
    return logging.getLogger(
        f"{LOGGER_PREFIX}.{module_name.rsplit('.', 1)[-1]}"
    )
