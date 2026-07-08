"""Shared outbound HTTP helper for import providers.

Import providers talk to a third-party signage vendor's own API during a
migration *away* from that vendor. A self-identifying ``Anthias/<version>``
User-Agent (what :class:`anthias_common.http.AnthiasSession` sends) invites
vendor-side blocking, so these calls deliberately do **not** use it — they
carry a neutral browser-like UA instead, the same blend-in rationale as the
anti-bot probe in ``anthias_common.utils.url_fails``.

Every provider builds its session through :func:`new_import_session` so the
UA policy lives in exactly one place.
"""

from __future__ import annotations

import requests

# Neutral, non-Anthias User-Agent. See the module docstring for why we don't
# use AnthiasSession here.
IMPORT_USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)


def new_import_session() -> requests.Session:
    """Return a ``requests.Session`` carrying the neutral import UA."""
    session = requests.Session()
    session.headers['User-Agent'] = IMPORT_USER_AGENT
    return session
