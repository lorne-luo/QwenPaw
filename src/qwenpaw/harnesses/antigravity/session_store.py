# -*- coding: utf-8 -*-
"""Durable mappings; provider conversations are never deleted here."""
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, state_dir: Path):
        self.path = state_dir / 'antigravity_sessions.json'
        self.sessions = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if data.get('version') != 1:
                    raise ValueError(
                        'Unsupported Antigravity session store version',
                    )
                self.sessions = dict(data['sessions'])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                # One unreadable file must not take every harness offline,
                # but the next save() must not overwrite it either: keep it
                # aside so the mappings can still be recovered by hand.
                logger.warning(
                    'Ignoring unreadable Antigravity session store %s',
                    self.path,
                    exc_info=True,
                )
                self._set_aside()

    def _set_aside(self):
        backup = self.path.with_name(f'{self.path.name}.unreadable')
        try:
            os.replace(self.path, backup)
        except OSError:
            logger.warning(
                'Could not move aside %s',
                self.path,
                exc_info=True,
            )
        else:
            logger.warning('Moved unreadable session store to %s', backup)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'version': 1, 'sessions': self.sessions}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
