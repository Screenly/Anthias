"""Tests for ``anthias_server.app.startup.run_envelope_check``.

The hook fires once per server start (wired via
``AnthiasAppConfig.ready``). Its job is to compare the cached
envelope against what we'd compute now and queue the celery
walker if they differ. The behaviour we're locking in:

* ``ENVIRONMENT=test`` / ``PYTEST_CURRENT_TEST`` short-circuit
  before touching celery — we don't want every pytest run to
  enqueue a catalog walk.
* Missing cache → save + dispatch (first-ever start, or
  cache file deleted by an operator).
* Cache matches current → no save, no dispatch, no work.
* Cache differs from current → save the fresh value first
  (so even a failed dispatch leaves the cache correct for the
  next start), then dispatch the walker.
* Save / dispatch failures are logged but never raise — the hook
  is a maintenance pass, not a startup gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from anthias_server.app import startup
from anthias_server.playback_envelope import PlaybackEnvelope


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the envelope cache to a tmpdir for each test.

    ``run_envelope_check`` calls into ``playback_envelope.load_cached``
    / ``save_cached``, which read ``settings.get_configdir()`` which
    in turn reads ``$HOME``. Pointing ``$HOME`` at a tmpdir keeps
    the test from clobbering the operator's actual cache.
    """
    home = tmp_path / 'home'
    (home / '.anthias').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    # Make sure we're not flagged as a test run by the hook itself —
    # the tests below want to exercise the real code path, not the
    # short-circuit. We delete both keys it checks.
    monkeypatch.delenv('ENVIRONMENT', raising=False)
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    return home / '.anthias'


def _patched_dispatch() -> Any:
    """Patch the celery task ``.delay`` attribute the hook calls.

    We patch at the import path the hook resolves
    (``anthias_server.celery_tasks.regenerate_for_envelope_change``)
    so the mock catches the call regardless of whether the test
    has imported the task itself.
    """
    return mock.patch(
        'anthias_server.celery_tasks.regenerate_for_envelope_change.delay'
    )


def test_short_circuits_when_environment_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ENVIRONMENT=test`` returns immediately — no cache read, no
    dispatch. The pytest harness sets this env var on every Anthias
    test run; the hook must respect it."""
    monkeypatch.setenv('ENVIRONMENT', 'test')
    with (
        _patched_dispatch() as dispatch,
        mock.patch('anthias_server.playback_envelope.load_cached') as load,
    ):
        startup.run_envelope_check()
    dispatch.assert_not_called()
    load.assert_not_called()


def test_short_circuits_when_pytest_current_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``PYTEST_CURRENT_TEST`` env var is set by pytest for the
    duration of each test. The hook short-circuits on it too, so a
    test that *forgets* to clear it never enqueues a stray walker."""
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'foo.py::test_bar')
    with (
        _patched_dispatch() as dispatch,
        mock.patch('anthias_server.playback_envelope.load_cached') as load,
    ):
        startup.run_envelope_check()
    dispatch.assert_not_called()
    load.assert_not_called()


def test_no_cache_writes_and_dispatches(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-ever start (no cache file) — write the computed value
    + dispatch the walker. ``compute_envelope`` resolves from
    ``DEVICE_TYPE``, so pin it to a known board."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    with _patched_dispatch() as dispatch:
        startup.run_envelope_check()
    dispatch.assert_called_once()
    # Cache file written.
    assert (fake_home / 'playback-envelope.json').is_file()


def test_cache_matches_skips_dispatch(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache equals current envelope → no save, no dispatch, no
    work. Catches a regression where the hook spuriously rewrites
    the cache on every start (would log + dispatch every boot)."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    # Pre-seed the cache with the pi5 envelope.
    from anthias_server.playback_envelope import save_cached

    save_cached(PlaybackEnvelope('hevc', 3840, 2160, 60))
    cache_path = fake_home / 'playback-envelope.json'
    mtime_before = cache_path.stat().st_mtime

    with _patched_dispatch() as dispatch:
        startup.run_envelope_check()

    dispatch.assert_not_called()
    # Cache file untouched (no spurious rewrite).
    assert cache_path.stat().st_mtime == mtime_before


def test_cache_differs_saves_then_dispatches(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Envelope changed (board swap, Anthias upgrade) → save first,
    then dispatch. The save-first ordering matters: if the dispatch
    later fails, the next start sees the new envelope on disk and
    decides "no change → no work" rather than re-dispatching."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    from anthias_server.playback_envelope import save_cached

    # Pre-seed the cache with a *different* envelope (default H.264
    # 1080p30). The hook should detect the mismatch and rewrite.
    save_cached(PlaybackEnvelope('h264', 1920, 1080, 30))
    cache_path = fake_home / 'playback-envelope.json'

    with _patched_dispatch() as dispatch:
        startup.run_envelope_check()

    dispatch.assert_called_once()
    # Cache now matches the computed pi5 envelope.
    import json

    written = json.loads(cache_path.read_text())
    assert written == {
        'codec': 'hevc',
        'max_width': 3840,
        'max_height': 2160,
        'max_fps': 60,
    }


def test_dispatch_failure_is_logged_not_raised(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Celery broker down → log + continue. The hook is a
    maintenance pass; a server start mustn't die because the worker
    queue is temporarily unreachable. The cache is already saved at
    this point, so the next start sees "no change" and behaves
    correctly even without the walker having fired."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    with mock.patch(
        'anthias_server.celery_tasks.regenerate_for_envelope_change.delay',
        side_effect=RuntimeError('redis unreachable'),
    ):
        # The hook must NOT raise.
        startup.run_envelope_check()
    # Cache still written despite the dispatch failure.
    assert (fake_home / 'playback-envelope.json').is_file()


def test_corrupt_cache_treated_as_missing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage cache file behaves like first-start: ``load_cached``
    returns ``None``, the hook saves fresh + dispatches. This is the
    "operator hand-edit broke the JSON" recovery path."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    (fake_home / 'playback-envelope.json').write_text('not json {{{')

    with _patched_dispatch() as dispatch:
        startup.run_envelope_check()

    dispatch.assert_called_once()
