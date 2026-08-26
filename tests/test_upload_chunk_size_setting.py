"""Tests for ANTHIAS_UPLOAD_CHUNK_SIZE_MB parsing in the Django settings.

The variable is set once through the balena dashboard and rarely
looked at again, so a trailing space or a stray unit (``16m``) must
not raise ValueError while the settings module imports — that would
stop the container from coming up on a device with no shell to
diagnose it. The bounds are enforced here as well as in the browser
so the client cap is a second line of defence, not the only one.
"""

import logging

import pytest

from anthias_server.django_project.settings import (
    UPLOAD_CHUNK_SIZE_MB_DEFAULT,
    UPLOAD_CHUNK_SIZE_MB_MAX,
    UPLOAD_CHUNK_SIZE_MB_MIN,
    resolve_upload_chunk_size_mb,
)


class TestResolveUploadChunkSizeMb:
    def test_unset_uses_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', raising=False)
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_DEFAULT

    def test_a_plain_value_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '8')
        assert resolve_upload_chunk_size_mb() == 8

    def test_surrounding_whitespace_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', ' 8 ')
        assert resolve_upload_chunk_size_mb() == 8

    @pytest.mark.parametrize('value', ['16m', '', 'sixteen', '8.5', '0x10'])
    def test_an_unparseable_value_falls_back_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', value)
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_DEFAULT

    def test_an_unparseable_value_says_so(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '16m')
        with caplog.at_level(logging.WARNING):
            resolve_upload_chunk_size_mb()
        assert '16m' in caplog.text

    def test_a_value_above_the_ceiling_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '32')
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_MAX

    def test_a_value_below_the_floor_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '0')
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_MIN

    def test_a_negative_value_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '-4')
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_MIN

    def test_a_clamped_value_says_so(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', '32')
        with caplog.at_level(logging.WARNING):
            resolve_upload_chunk_size_mb()
        assert str(UPLOAD_CHUNK_SIZE_MB_MAX) in caplog.text
