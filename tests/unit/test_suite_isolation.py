# -*- coding: utf-8 -*-
"""Guard: the suite must never attach to a developer's live instance.

qwenpaw.constant freezes WORKING_DIR / SECRET_DIR at import time, so the
redirect lives at the top of tests/conftest.py rather than in a fixture. If
that ordering is ever broken -- an import hoisted above it, the env
assignment dropped -- tests silently start reading and writing the real
~/.qwenpaw: real session files, real MCP servers, and the real cron
scheduler firing real jobs mid-run. These assertions turn that back into a
loud failure.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_working_dir_is_redirected_to_a_throwaway_directory() -> None:
    from qwenpaw.constant import WORKING_DIR

    expected = Path(os.environ["QWENPAW_WORKING_DIR"]).resolve()
    assert WORKING_DIR == expected


def test_qwenpaw_dirs_escape_the_real_home() -> None:
    from qwenpaw.constant import SECRET_DIR, WORKING_DIR

    real_homes = [
        Path("~/.qwenpaw").expanduser().resolve(),
        Path("~/.copaw").expanduser().resolve(),
    ]
    for real_home in real_homes:
        for path in (WORKING_DIR, SECRET_DIR):
            assert path != real_home
            assert real_home not in path.parents
