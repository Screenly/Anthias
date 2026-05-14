"""Tests for ``anthias_server.playback_envelope``.

The envelope is the canonical "what every video on disk must
look like" per device. The matrix is the source of truth three
other modules read from (`processing.py`, `celery_tasks.py`'s
walker, `media_player.py`'s safety-net dispatch), so the tests
here lock in:

* every supported board → an envelope that matches the
  documented matrix;
* unknown / unset ``DEVICE_TYPE`` → the conservative default;
* cache round-trip: ``save_cached(e); load_cached() == e``;
* cache corruption (missing file, bad JSON, malformed payload) →
  ``load_cached() == None`` so the caller computes fresh and
  overwrites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from anthias_server.playback_envelope import (
    ENVELOPE_BY_DEVICE_TYPE,
    PlaybackEnvelope,
    compute_envelope,
    load_cached,
    save_cached,
)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the envelope cache to a tmpdir.

    The module reads ``settings.get_configdir()`` to find its
    persistent state. The simplest, side-effect-free override is to
    point ``$HOME`` at a tmp dir + create the ``.anthias`` subdir
    inside it. ``AnthiasSettings`` re-reads ``$HOME`` on every
    method call.
    """
    home = tmp_path / 'home'
    (home / '.anthias').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    return home / '.anthias'


@pytest.mark.parametrize(
    ('device_type', 'codec', 'max_w', 'max_h', 'max_fps'),
    [
        # H.264 1080p30 boards (no HEVC silicon / no mpv HW path).
        ('pi2', 'h264', 1920, 1080, 30),
        ('pi3', 'h264', 1920, 1080, 30),
        ('arm64', 'h264', 1920, 1080, 30),
        # HEVC 4Kp60 boards (dedicated HEVC block or VAAPI).
        ('pi4-64', 'hevc', 3840, 2160, 60),
        ('pi5', 'hevc', 3840, 2160, 60),
        ('x86', 'hevc', 3840, 2160, 60),
    ],
)
def test_envelope_matrix_per_device_type(
    device_type: str,
    codec: str,
    max_w: int,
    max_h: int,
    max_fps: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every documented board resolves to its envelope exactly.

    The matrix is the canonical source of truth — a silent drift
    here would split codec policy between the asset processor and
    the viewer's hwdec dispatch (which is exactly the kind of bug
    `bb27b186` was meant to close once and for all). Pin every row.
    """
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    e = compute_envelope()
    assert e == PlaybackEnvelope(codec, max_w, max_h, max_fps)
    # Also verify the dict carries the same value the test pinned.
    assert ENVELOPE_BY_DEVICE_TYPE[device_type] == e


def test_envelope_unset_device_type_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env → safe H.264 1080p30 default.

    The dev host (no DEVICE_TYPE) and `ENVIRONMENT=test` both hit
    this path. H.264 1080p is the lowest common denominator any
    Anthias-supported board can play in software.
    """
    monkeypatch.delenv('DEVICE_TYPE', raising=False)
    assert compute_envelope() == PlaybackEnvelope('h264', 1920, 1080, 30)


def test_envelope_unknown_device_type_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised board name (typo, future board) falls back
    to the conservative default rather than crashing — Anthias
    should keep working, just at the safe codec, until the matrix
    learns the new key."""
    monkeypatch.setenv('DEVICE_TYPE', 'weird-future-board-2030')
    assert compute_envelope() == PlaybackEnvelope('h264', 1920, 1080, 30)


def test_envelope_device_type_case_and_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_envelope`` lowercases + strips so a stray newline
    in the env file or a mixed-case board key doesn't fall through
    to the default by accident. Matches the same normalisation the
    legacy `_resolve_board_profile` did."""
    monkeypatch.setenv('DEVICE_TYPE', '  PI5\n')
    assert compute_envelope() == ENVELOPE_BY_DEVICE_TYPE['pi5']


def test_envelope_dataclass_round_trip() -> None:
    """``PlaybackEnvelope.from_dict(e.as_dict()) == e`` is the
    foundation the JSON cache and the per-asset
    ``metadata['envelope']`` field rely on for equality
    comparison."""
    e = PlaybackEnvelope('hevc', 3840, 2160, 60)
    assert PlaybackEnvelope.from_dict(e.as_dict()) == e


@pytest.mark.parametrize(
    'payload',
    [
        # Missing required key.
        {'codec': 'hevc', 'max_width': 3840, 'max_height': 2160},
        # Non-integer dimension.
        {
            'codec': 'hevc',
            'max_width': 'big',
            'max_height': 2160,
            'max_fps': 60,
        },
        # Unsupported codec (would silently route to wrong hwdec).
        {'codec': 'vp9', 'max_width': 1920, 'max_height': 1080, 'max_fps': 30},
    ],
)
def test_envelope_from_dict_rejects_malformed(payload: dict[str, Any]) -> None:
    """A corrupt cache must not yield a half-valid envelope — the
    caller treats ``ValueError`` as "no cache" and recomputes from
    the matrix. This is how a hand-edit that breaks the JSON
    self-heals on next start."""
    with pytest.raises(ValueError):
        PlaybackEnvelope.from_dict(payload)


def test_cache_round_trip(cache_dir: Path) -> None:
    """``save_cached(e); load_cached() == e`` for every envelope
    shape we'd realistically write. The serialisation format is
    pinned (codec / max_width / max_height / max_fps) so an
    operator hand-editing the JSON sees a predictable schema."""
    e = PlaybackEnvelope('hevc', 3840, 2160, 60)
    save_cached(e)
    assert load_cached() == e

    written = json.loads((cache_dir / 'playback-envelope.json').read_text())
    assert written == {
        'codec': 'hevc',
        'max_width': 3840,
        'max_height': 2160,
        'max_fps': 60,
    }


def test_load_cached_returns_none_when_file_missing(cache_dir: Path) -> None:
    """First-ever start has no cache file. The caller computes
    fresh and writes — we don't want ``load_cached`` to raise."""
    assert load_cached() is None


def test_load_cached_returns_none_on_corrupt_json(cache_dir: Path) -> None:
    """A hand-edit that breaks JSON parsing self-heals: we treat
    it as missing and the caller recomputes + overwrites. No
    operator intervention needed beyond a server restart."""
    (cache_dir / 'playback-envelope.json').write_text('this is not json {{{')
    assert load_cached() is None


def test_load_cached_returns_none_on_invalid_payload(cache_dir: Path) -> None:
    """Same recovery contract for parseable JSON whose payload
    doesn't validate (e.g. someone added ``codec: vp9`` — outside
    the supported set). Better to drop a malformed envelope than
    have the walker re-render the entire catalog against it."""
    (cache_dir / 'playback-envelope.json').write_text(
        json.dumps(
            {
                'codec': 'vp9',
                'max_width': 1920,
                'max_height': 1080,
                'max_fps': 30,
            }
        )
    )
    assert load_cached() is None


def test_save_cached_atomic(cache_dir: Path) -> None:
    """``save_cached`` uses temp-file + rename so a crash mid-write
    can't leave a half-written file. We can't directly observe the
    rename, but we can verify the tmp file isn't left behind after
    a successful save."""
    save_cached(PlaybackEnvelope('hevc', 3840, 2160, 60))
    leftover = list(cache_dir.glob('*.tmp'))
    assert not leftover, f'staging file leaked: {leftover}'


def test_save_cached_overwrites_existing(cache_dir: Path) -> None:
    """An envelope change on Anthias upgrade rewrites the cache.
    Verify the second write replaces the first (no append, no
    leftover state)."""
    save_cached(PlaybackEnvelope('h264', 1920, 1080, 30))
    save_cached(PlaybackEnvelope('hevc', 3840, 2160, 60))
    assert load_cached() == PlaybackEnvelope('hevc', 3840, 2160, 60)


def test_container_ext_is_mp4() -> None:
    """The container stays fixed across every envelope so the
    variant-on-disk filename convention (`<id>.mp4`) never gains a
    per-board exception. Every Anthias-supported player handles
    mp4 natively, so this is a deliberate one-way constraint."""
    for envelope in ENVELOPE_BY_DEVICE_TYPE.values():
        assert envelope.container_ext == 'mp4'
