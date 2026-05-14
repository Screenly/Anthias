"""Office Space-themed sample assets shared by the integration suite.

A single source of truth so test_app.py, test_migrate_to_screenly.py
and any future Playwright test render the same brand-consistent
content. When ``MARKETING_SCREENSHOTS=1`` is set the same data lights
up high-DPI captures via the ``marketing_screenshot`` fixture in
``tests/conftest.py``; in the default integration run the seeds are
just the data the tests work against, no different from the bare
dicts they replaced.

The parody is deliberately unmistakable without using any registered
trademark or copyrighted material — every name is a generic riff on
the source film, and URIs use ``example.com`` plus made-up local
paths. ``asset_id`` values match the originals so tests that key off
them (e.g. the migration wizard's per-asset call-log assertion) keep
working unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone


def _now_window() -> dict[str, Any]:
    """Yesterday → tomorrow so every seed renders on the home page.

    Module-level seed constants below call ``_seed()`` (and therefore
    ``_now_window()``) at import time, so their ``start_date`` /
    ``end_date`` are captured against wall-clock at the moment this
    module first loads. ``home_seed_assets()`` is a function and
    re-evaluates the window on each call. No current test combines
    these singletons with ``time_machine``; a test that needs to
    travel time should construct fresh seeds via ``_seed()`` or use
    the factory rather than the singletons."""
    return {
        'start_date': timezone.now() - timedelta(days=1),
        'end_date': timezone.now() + timedelta(days=1),
    }


def _seed(
    *,
    asset_id: str,
    name: str,
    mimetype: str,
    uri: str,
    duration: int = 8,
    is_enabled: int = 1,
    play_order: int = 0,
) -> dict[str, Any]:
    return {
        **_now_window(),
        'asset_id': asset_id,
        'name': name,
        'mimetype': mimetype,
        'uri': uri,
        'duration': duration,
        'is_enabled': is_enabled,
        'nocache': 0,
        'play_order': play_order,
        'skip_asset_check': 0,
    }


# ---------------------------------------------------------------------------
# Home-page singletons
#
# ``asset_id`` values are preserved verbatim from the pre-rename dicts
# in test_app.py so any per-id assertion keeps matching. Mimetype short
# codes ('image' / 'web') likewise match the originals — the URL-form
# handler stores 'webpage', but several smoke tests fixture rows in
# directly with 'web', and changing that would shift behaviour beyond
# the rename.
# ---------------------------------------------------------------------------


INITECH_ANNOUNCEMENT = _seed(
    asset_id='7e978f8c1204a6f70770a1eb54a76e9b',
    name='Initech — Q4 All-Hands Recap',
    mimetype='image',
    uri='https://example.com/initech/q4-allhands-recap.png',
    duration=6,
    play_order=0,
)

LUMBERGH_MEMO = _seed(
    asset_id='4c8dbce552edb5812d3a866cfe5f159d',
    name='Memo: TPS Cover Sheet — Effective Immediately',
    mimetype='web',
    uri='https://example.com/initech/tps-coversheet-memo',
    duration=10,
    play_order=1,
)

CHOTCHKIES_FLAIR_POLICY = _seed(
    asset_id='aa11bb22cc33dd44ee55ff6677889900',
    name="Chotchkie's — Pieces of Flair Policy",
    mimetype='web',
    uri='https://example.com/chotchkies/flair-policy',
    duration=5,
    is_enabled=0,
    play_order=99,
)


def home_seed_assets() -> list[dict[str, Any]]:
    """A representative 6-asset schedule for marketing-quality home
    captures and the new ``test_home_renders_with_full_schedule``
    layout-regression test. Mix of mimetypes, durations and an
    explicit disabled row so the table renders every visual branch
    in one go."""
    return [
        INITECH_ANNOUNCEMENT,
        LUMBERGH_MEMO,
        _seed(
            asset_id='b1d31a8f2e7c4d5a9f6b2e1c8d3f0a4e',
            name='Milton — Stapler Inventory Audit',
            mimetype='image',
            uri='/data/anthias_assets/stapler-audit.png',
            duration=5,
            play_order=2,
        ),
        _seed(
            asset_id='c2e42b9f3d8e5a6b0c7d3f2a9b4e1d5f',
            name='Office Olympics — Sign-up Open',
            mimetype='web',
            uri='https://example.com/initech/olympics',
            duration=12,
            play_order=3,
        ),
        _seed(
            asset_id='d3f53cad4e9f6b7c1d8e4a3b0c5f2e6a',
            name="Chotchkie's — Tuesday Lunch Special",
            mimetype='image',
            uri='/data/anthias_assets/chotchkies-tuesday.png',
            duration=7,
            play_order=4,
        ),
        CHOTCHKIES_FLAIR_POLICY,
    ]


# ---------------------------------------------------------------------------
# Migration-wizard seeds (image + video + URL-backed webpage)
#
# Identifiers a1/a2/a3 are referenced directly by the wizard test's
# call_log assertions ('a1': 1, 'a2': 2, 'a3': 1 etc.), so they stay
# short. ``WIZARD_VIDEO_BASENAME`` / ``WIZARD_IMAGE_BASENAME`` /
# ``WIZARD_WEBPAGE_URL`` are exported as named constants so the
# wizard test's "displayUri strips /data/anthias_assets/ prefix"
# assertions don't have to hardcode the literal again.
# ---------------------------------------------------------------------------


WIZARD_VIDEO_BASENAME = 'initech-allhands-q4.mp4'
WIZARD_IMAGE_BASENAME = 'welcome-pgibbons.png'
WIZARD_WEBPAGE_URL = 'https://example.com/initech/conf-b'


WIZARD_VIDEO = _seed(
    asset_id='a1',
    name='Quarterly All-Hands — Q4',
    mimetype='video',
    uri=f'/data/anthias_assets/{WIZARD_VIDEO_BASENAME}',
)

WIZARD_WEBPAGE = _seed(
    asset_id='a2',
    name='Conference Room B — Today',
    mimetype='webpage',
    uri=WIZARD_WEBPAGE_URL,
)

WIZARD_IMAGE = _seed(
    asset_id='a3',
    name='Welcome — New Hire: Peter Gibbons',
    mimetype='image',
    uri=f'/data/anthias_assets/{WIZARD_IMAGE_BASENAME}',
)


WIZARD_SEED_ASSETS = [WIZARD_VIDEO, WIZARD_WEBPAGE, WIZARD_IMAGE]
