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

from anthias_common.version import get_anthias_release


ANTHIAS_HOMEPAGE = 'https://anthias.screenly.io'


def get_anthias_product_token() -> str:
    """Return the ``Anthias/<release>`` product token.

    Single source of truth for the ``Anthias/<version>`` label. Shared
    by :func:`get_user_agent` (which wraps it with the ``(+homepage)``
    comment for pure-HTTP clients) and the C++ webview, which appends
    the bare token to Chromium's browser ``User-Agent`` via the
    ``ANTHIAS_UA_TOKEN`` env var — browser UAs conventionally carry a
    bare ``Product/Version`` token rather than a ``(+URL)`` comment.
    Falls back to ``unknown`` only when ``get_anthias_release()`` finds
    neither the installed package metadata nor the repo-root
    pyproject.toml.
    """
    release = get_anthias_release() or 'unknown'
    return f'Anthias/{release}'


def get_user_agent() -> str:
    """Return the canonical Anthias ``User-Agent`` string.

    Format: ``Anthias/<release> (+<homepage>)`` — the standard
    ``Product/Version (+URL)`` convention used by well-behaved
    crawlers so ops at the receiving end can identify the source
    without parsing the path. Builds on
    :func:`get_anthias_product_token` so the ``Anthias/<release>`` label
    (and its ``unknown`` fallback) lives in exactly one place.
    """
    return f'{get_anthias_product_token()} (+{ANTHIAS_HOMEPAGE})'


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
