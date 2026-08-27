"""Tests for ANTHIAS_UPLOAD_CHUNK_SIZE_MB parsing in the Django settings.

The variable is set once through the balena dashboard and rarely
looked at again, so a trailing space or a stray unit (``16m``) must
not raise ValueError while the settings module imports — that would
stop the container from coming up on a device with no shell to
diagnose it. The bounds are enforced here as well as in the browser
so the client cap is a second line of defence, not the only one.
"""

import logging
from pathlib import Path

import pytest
import yaml

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

    @pytest.mark.parametrize(
        'value', ['16m', 'sixteen', '0x10', 'nan', 'inf', '9' * 5000]
    )
    def test_an_unparseable_value_falls_back_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', value)
        assert resolve_upload_chunk_size_mb() == UPLOAD_CHUNK_SIZE_MB_DEFAULT

    @pytest.mark.parametrize('value', ['', '   '])
    def test_an_empty_value_uses_the_default(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Not a hypothetical: docker-compose.yml.tmpl always sets the
        variable, and it renders empty for every operator who has not
        chosen a value."""
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

    @pytest.mark.parametrize(
        ('value', 'expected'),
        [('8.5', 8), ('1.9', 1), ('23.9', 23), ('0.5', 1)],
    )
    def test_a_fractional_value_rounds_down_not_up(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: int
    ) -> None:
        """MB is a decimal quantity, and this knob only ever exists to
        get *under* a proxy's cap. An operator who writes 8.5 to fit a
        10 MB limit must not be handed 16."""
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', value)
        assert resolve_upload_chunk_size_mb() == expected

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


class TestUploadChunkSizeIsReachable:
    """The variable has to survive the trip from the operator to Django.

    A setting only settings.py knows about is unreachable on the plain
    docker-compose install: the template lists anthias-server's
    environment explicitly, Compose passes only what it declares, and
    upgrade_containers.sh regenerates the file from that template on
    every run. This is the install the chunking exists for — balena
    devices get dashboard variables injected into every service.
    """

    def test_compose_template_passes_the_variable_to_the_server(
        self,
    ) -> None:
        template = (
            Path(__file__).resolve().parents[1] / 'docker-compose.yml.tmpl'
        )
        services = yaml.safe_load(template.read_text())['services']
        assert (
            'ANTHIAS_UPLOAD_CHUNK_SIZE_MB=${ANTHIAS_UPLOAD_CHUNK_SIZE_MB}'
            in services['anthias-server']['environment']
        )

    def test_upgrade_sources_the_operator_env_file(self) -> None:
        """Without this, a value set on the device is reverted by the
        next upgrade and the 413s come back unexplained."""
        script = (
            Path(__file__).resolve().parents[1]
            / 'bin'
            / 'upgrade_containers.sh'
        ).read_text()
        sourcing = script.index('. /etc/anthias/anthias.env')
        rendering = script.index('| envsubst')
        assert sourcing < rendering
