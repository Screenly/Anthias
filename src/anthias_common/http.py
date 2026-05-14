"""Shared HTTP client helpers for outbound Anthias requests.

A central place to set the ``User-Agent`` so any service Anthias
talks to (Screenly's migration API today; future third-party calls
later) sees a consistent ``Anthias/<version>`` label rather than the
default ``python-requests/<x.y>``.

Spoofed-UA traffic (the URL-availability probe in
``anthias_common.utils`` that pretends to be Safari to dodge anti-bot
pages) deliberately bypasses this — it sets its own header inline.
"""

from __future__ import annotations

import requests

from anthias_server.lib import diagnostics


ANTHIAS_HOMEPAGE = 'https://anthias.screenly.io'


def get_user_agent() -> str:
    """Return the canonical Anthias ``User-Agent`` string.

    Format: ``Anthias/<release> (+<homepage>)`` — the standard
    ``Product/Version (+URL)`` convention used by well-behaved
    crawlers so ops at the receiving end can identify the source
    without parsing the path. Falls back to ``unknown`` only when
    ``diagnostics.get_anthias_release()`` finds neither the installed
    package metadata nor the repo-root pyproject.toml.
    """
    release = diagnostics.get_anthias_release() or 'unknown'
    return f'Anthias/{release} (+{ANTHIAS_HOMEPAGE})'


class AnthiasSession(requests.Session):
    """A ``requests.Session`` pre-configured with the Anthias UA.

    Use this anywhere Anthias makes an outbound call to a third party
    so Screenly / GitHub / etc. see a consistent, identifiable label
    instead of the default ``python-requests/<x.y>``. Per-call
    ``headers={'User-Agent': ...}`` still wins — ``requests`` merges
    request headers over session headers on each call.
    """

    def __init__(self) -> None:
        super().__init__()
        self.headers['User-Agent'] = get_user_agent()
