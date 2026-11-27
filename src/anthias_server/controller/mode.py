"""Whether this install runs as a multi-screen controller.

Kept apart from ``controller.service`` so the periodic controller tasks,
which run on every screen and return at once when controller mode is
off, can ask without importing the PKI stack (``cryptography``, about
15 MB per process on a 64-bit Pi) into every Celery process.
"""

from __future__ import annotations


def controller_enabled() -> bool:
    from anthias_server.settings import settings

    # Re-read: the toggle is saved by the web process, and this also
    # runs in the Celery worker.
    settings.load()
    return bool(settings['controller_mode']) and bool(settings['auth_backend'])
