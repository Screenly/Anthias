"""Tests for the fault-tolerant WhiteNoise startup scan.

Regression coverage for Sentry ANTHIAS-Y: a balena OTA rewrote the
staticfiles layer onto a device with corrupted ext4 metadata, the
stock WhiteNoise scan raised ``OSError: [Errno 117] Structure needs
cleaning`` at ASGI import, and uvicorn crash-looped — bricking the
device over one unreadable Django-admin vendor file.
"""

import logging
import os
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings

from anthias_server.lib.whitenoise import ResilientWhiteNoiseMiddleware


def _make_tree(root: Path) -> None:
    (root / 'css').mkdir(parents=True)
    (root / 'css' / 'app.css').write_text('body{}')
    (root / 'js').mkdir()
    (root / 'js' / 'app.js').write_text('//')


class TestScantreeTolerant:
    def test_yields_all_entries_on_healthy_tree(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        skipped: list[tuple[str, OSError]] = []
        found = dict(
            ResilientWhiteNoiseMiddleware._scantree_tolerant(
                str(tmp_path), skipped
            )
        )
        assert skipped == []
        assert {os.path.basename(p) for p in found} == {
            'app.css',
            'app.js',
        }

    def test_unlistable_directory_is_skipped_not_fatal(
        self, tmp_path: Path
    ) -> None:
        _make_tree(tmp_path)
        bad_dir = str(tmp_path / 'js')
        real_scandir = os.scandir

        def fake_scandir(path: str):  # type: ignore[no-untyped-def]
            if str(path) == bad_dir:
                raise OSError(117, 'Structure needs cleaning', path)
            return real_scandir(path)

        skipped: list[tuple[str, OSError]] = []
        with mock.patch(
            'anthias_server.lib.whitenoise.os.scandir', fake_scandir
        ):
            found = dict(
                ResilientWhiteNoiseMiddleware._scantree_tolerant(
                    str(tmp_path), skipped
                )
            )
        # The css file survives; the corrupted dir is recorded.
        assert {os.path.basename(p) for p in found} == {'app.css'}
        assert len(skipped) == 1
        assert skipped[0][0] == bad_dir
        assert skipped[0][1].errno == 117

    def test_unstattable_entry_is_skipped_not_fatal(
        self, tmp_path: Path
    ) -> None:
        # The exact ANTHIAS-Y shape: scandir lists the entry but the
        # corrupted inode fails on stat().
        _make_tree(tmp_path)
        bad_file = str(tmp_path / 'js' / 'app.js')
        real_scandir = os.scandir

        class _Entry:
            def __init__(self, entry: os.DirEntry[str]) -> None:
                self._entry = entry
                self.path = entry.path

            def is_dir(self) -> bool:
                return self._entry.is_dir()

            def stat(self) -> os.stat_result:
                if self.path == bad_file:
                    raise OSError(117, 'Structure needs cleaning', self.path)
                return self._entry.stat()

        def fake_scandir(path: str) -> list[_Entry]:
            return [_Entry(e) for e in real_scandir(path)]

        skipped: list[tuple[str, OSError]] = []
        with mock.patch(
            'anthias_server.lib.whitenoise.os.scandir', fake_scandir
        ):
            found = dict(
                ResilientWhiteNoiseMiddleware._scantree_tolerant(
                    str(tmp_path), skipped
                )
            )
        assert {os.path.basename(p) for p in found} == {'app.css'}
        assert [p for p, _ in skipped] == [bad_file]


class TestMiddlewareDegradesGracefully:
    def _middleware(self, root: Path) -> ResilientWhiteNoiseMiddleware:
        # Instantiate against a scratch STATIC_ROOT the way Django's
        # handler does at startup; autorefresh off = the one-shot
        # scan that crashed in the field.
        with mock.patch.object(settings, 'STATIC_ROOT', str(root)):
            return ResilientWhiteNoiseMiddleware(
                get_response=lambda request: None
            )

    def test_survives_corrupted_subtree_and_serves_the_rest(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _make_tree(tmp_path)
        bad_dir = str(tmp_path / 'js')
        real_scandir = os.scandir

        def fake_scandir(path: str):  # type: ignore[no-untyped-def]
            if str(path) == bad_dir:
                raise OSError(117, 'Structure needs cleaning', path)
            return real_scandir(path)

        with (
            mock.patch(
                'anthias_server.lib.whitenoise.os.scandir', fake_scandir
            ),
            caplog.at_level(logging.ERROR),
        ):
            middleware = self._middleware(tmp_path)

        # No exception escaped, the healthy file is served under
        # its canonical /static/ URL (no double-slash from a root
        # without a trailing separator)...
        assert '/static/css/app.css' in middleware.files
        # ...and exactly one ERROR records the storage fault.
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert 'Structure needs cleaning' in errors[0].getMessage()
        assert 'storage' in errors[0].getMessage()

    def test_no_error_log_on_healthy_tree(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _make_tree(tmp_path)
        with caplog.at_level(logging.ERROR):
            middleware = self._middleware(tmp_path)
        assert any('app.css' in url for url in middleware.files)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_settings_use_the_resilient_middleware() -> None:
    assert (
        'anthias_server.lib.whitenoise.ResilientWhiteNoiseMiddleware'
        in settings.MIDDLEWARE
    )
    assert 'whitenoise.middleware.WhiteNoiseMiddleware' not in (
        settings.MIDDLEWARE
    )
