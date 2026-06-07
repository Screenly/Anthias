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
from typing import Any
from unittest import mock

import pytest
from django.conf import settings
from django.http import HttpResponse

from anthias_server.lib.whitenoise import ResilientWhiteNoiseMiddleware


def _make_tree(root: Path) -> None:
    (root / 'css').mkdir(parents=True)
    (root / 'css' / 'app.css').write_text('body{}')
    (root / 'js').mkdir()
    (root / 'js' / 'app.js').write_text('//')


class _ScandirCM:
    """Context-manager iterable matching ``os.scandir``'s protocol,
    so fakes that hand back a custom entry list still work with the
    production ``with os.scandir(...) as it:`` usage."""

    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

    def __enter__(self) -> 'list[Any]':
        return self._entries

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Any:
        return iter(self._entries)


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

        def fake_scandir(path: str) -> _ScandirCM:
            with real_scandir(path) as it:
                return _ScandirCM([_Entry(e) for e in it])

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
    def _middleware(self) -> ResilientWhiteNoiseMiddleware:
        # Construct without scanning (the test settings enable
        # WHITENOISE_AUTOREFRESH under DEBUG, so __init__ doesn't scan
        # STATIC_ROOT), then drive the overridden scan directly — that
        # keeps the test pinned to our code rather than to whichever
        # startup path the active DEBUG/autorefresh config takes. The
        # get_response returns a real HttpResponse per Django's
        # middleware contract.
        return ResilientWhiteNoiseMiddleware(
            get_response=lambda request: HttpResponse()
        )

    def test_survives_corrupted_subtree_and_serves_the_rest(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _make_tree(tmp_path)
        bad_dir = str(tmp_path / 'js')
        real_scandir = os.scandir

        def fake_scandir(path: str) -> Any:
            if str(path) == bad_dir:
                raise OSError(117, 'Structure needs cleaning', path)
            return real_scandir(path)

        middleware = self._middleware()
        with (
            mock.patch(
                'anthias_server.lib.whitenoise.os.scandir', fake_scandir
            ),
            caplog.at_level(logging.ERROR),
        ):
            middleware.update_files_dictionary(str(tmp_path), '/static/')

        # No exception escaped, the healthy file is served under its
        # canonical /static/ URL (no double-slash from a root without
        # a trailing separator)...
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
        middleware = self._middleware()
        with caplog.at_level(logging.ERROR):
            middleware.update_files_dictionary(str(tmp_path), '/static/')
        assert '/static/css/app.css' in middleware.files
        assert '/static/js/app.js' in middleware.files
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_settings_use_the_resilient_middleware() -> None:
    assert (
        'anthias_server.lib.whitenoise.ResilientWhiteNoiseMiddleware'
        in settings.MIDDLEWARE
    )
    assert 'whitenoise.middleware.WhiteNoiseMiddleware' not in (
        settings.MIDDLEWARE
    )
