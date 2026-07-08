"""Tests for the host-timezone validation in the Django settings.

Regression coverage for the `US/Central` crash-loop: the host's
/etc/timezone can carry a legacy alias that the zoneinfo database
knows but the image's /usr/share/zoneinfo doesn't ship as a file
(trixie moved legacy aliases into tzdata-legacy). Django validates
TIME_ZONE against the on-disk tree, so the settings module must
apply the same check and fall back to UTC instead of letting Django
raise ValueError at startup.
"""

from pathlib import Path

import pytest

from anthias_server.django_project import settings as django_settings
from anthias_server.django_project.settings import (
    get_configured_time_zone,
    get_host_time_zone,
    is_valid_time_zone,
    resolve_time_zone,
)


def _write_timezone(tmp_path: Path, value: str) -> str:
    timezone_file = tmp_path / 'etc-timezone'
    timezone_file.write_text(value)
    return str(timezone_file)


def _zoneinfo_root(tmp_path: Path, *zone_files: str) -> Path:
    root = tmp_path / 'zoneinfo'
    for zone in zone_files:
        zone_path = root.joinpath(*zone.split('/'))
        zone_path.parent.mkdir(parents=True, exist_ok=True)
        zone_path.touch()
    root.mkdir(exist_ok=True)
    return root


class TestGetHostTimeZone:
    def test_valid_zone_present_on_disk(self, tmp_path: Path) -> None:
        assert (
            get_host_time_zone(
                timezone_file=_write_timezone(tmp_path, 'America/Chicago\n'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
            )
            == 'America/Chicago'
        )

    def test_zone_known_to_zoneinfo_but_missing_on_disk(
        self, tmp_path: Path
    ) -> None:
        # The Sentry crash: `US/Central` resolves via the tzdata
        # package, but the on-disk tree (Django's source of truth)
        # lacks the legacy alias.
        assert (
            get_host_time_zone(
                timezone_file=_write_timezone(tmp_path, 'US/Central\n'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
            )
            == 'UTC'
        )

    def test_unknown_zone_name(self, tmp_path: Path) -> None:
        assert (
            get_host_time_zone(
                timezone_file=_write_timezone(tmp_path, 'Not/AZone\n'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
            )
            == 'UTC'
        )

    def test_empty_timezone_file(self, tmp_path: Path) -> None:
        assert (
            get_host_time_zone(
                timezone_file=_write_timezone(tmp_path, '\n'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
            )
            == 'UTC'
        )

    def test_missing_timezone_file(self, tmp_path: Path) -> None:
        assert (
            get_host_time_zone(
                timezone_file=str(tmp_path / 'does-not-exist'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
            )
            == 'UTC'
        )

    def test_no_zoneinfo_root_skips_disk_check(self, tmp_path: Path) -> None:
        # Mirrors Django: when /usr/share/zoneinfo is absent the disk
        # check is skipped and the zoneinfo lookup alone decides.
        assert (
            get_host_time_zone(
                timezone_file=_write_timezone(tmp_path, 'America/Chicago\n'),
                zoneinfo_root=tmp_path / 'no-such-zoneinfo',
            )
            == 'America/Chicago'
        )


class TestIsValidTimeZone:
    def test_valid_zone_on_disk(self, tmp_path: Path) -> None:
        assert is_valid_time_zone(
            'America/Chicago',
            zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
        )

    def test_known_alias_missing_on_disk_is_invalid(
        self, tmp_path: Path
    ) -> None:
        # Same crash-loop guard as the host path: US/Central resolves
        # via tzdata but the on-disk tree lacks the legacy alias.
        assert not is_valid_time_zone(
            'US/Central',
            zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
        )

    def test_unknown_zone_is_invalid(self, tmp_path: Path) -> None:
        assert not is_valid_time_zone(
            'Mars/Phobos',
            zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago'),
        )

    def test_empty_string_is_invalid(self, tmp_path: Path) -> None:
        assert not is_valid_time_zone(
            '', zoneinfo_root=_zoneinfo_root(tmp_path, 'America/Chicago')
        )


def _write_conf(tmp_path: Path, value: str | None) -> str:
    conf = tmp_path / 'anthias.conf'
    body = '[main]\n'
    if value is not None:
        body += f'timezone = {value}\n'
    conf.write_text(body)
    return str(conf)


class TestGetConfiguredTimeZone:
    def test_valid_configured_zone(self, tmp_path: Path) -> None:
        assert (
            get_configured_time_zone(
                config_file=_write_conf(tmp_path, 'Europe/Stockholm'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'Europe/Stockholm'),
            )
            == 'Europe/Stockholm'
        )

    def test_blank_configured_zone_returns_none(self, tmp_path: Path) -> None:
        assert (
            get_configured_time_zone(
                config_file=_write_conf(tmp_path, ''),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'Europe/Stockholm'),
            )
            is None
        )

    def test_missing_key_returns_none(self, tmp_path: Path) -> None:
        assert (
            get_configured_time_zone(
                config_file=_write_conf(tmp_path, None),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'Europe/Stockholm'),
            )
            is None
        )

    def test_invalid_configured_zone_returns_none(
        self, tmp_path: Path
    ) -> None:
        # A bad hand-edit is ignored (falls through to the next rung)
        # rather than crash-looping the settings module.
        assert (
            get_configured_time_zone(
                config_file=_write_conf(tmp_path, 'Mars/Phobos'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'Europe/Stockholm'),
            )
            is None
        )

    def test_missing_config_file_returns_none(self, tmp_path: Path) -> None:
        assert (
            get_configured_time_zone(
                config_file=str(tmp_path / 'no-such.conf'),
                zoneinfo_root=_zoneinfo_root(tmp_path, 'Europe/Stockholm'),
            )
            is None
        )


class TestResolveTimeZone:
    """Precedence: config -> TZ env -> host -> UTC."""

    def test_config_wins_over_env_and_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            django_settings,
            'get_configured_time_zone',
            lambda: 'Europe/Stockholm',
        )
        monkeypatch.setenv('TZ', 'America/Chicago')
        monkeypatch.setattr(
            django_settings, 'get_host_time_zone', lambda: 'Asia/Tokyo'
        )
        assert resolve_time_zone() == 'Europe/Stockholm'

    def test_env_used_when_no_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            django_settings, 'get_configured_time_zone', lambda: None
        )
        monkeypatch.setenv('TZ', 'America/Chicago')
        monkeypatch.setattr(
            django_settings, 'get_host_time_zone', lambda: 'Asia/Tokyo'
        )
        assert resolve_time_zone() == 'America/Chicago'

    def test_invalid_env_falls_through_to_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            django_settings, 'get_configured_time_zone', lambda: None
        )
        monkeypatch.setenv('TZ', 'Mars/Phobos')
        monkeypatch.setattr(
            django_settings, 'get_host_time_zone', lambda: 'Asia/Tokyo'
        )
        assert resolve_time_zone() == 'Asia/Tokyo'

    def test_host_used_when_no_config_or_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            django_settings, 'get_configured_time_zone', lambda: None
        )
        monkeypatch.delenv('TZ', raising=False)
        monkeypatch.setattr(
            django_settings, 'get_host_time_zone', lambda: 'Asia/Tokyo'
        )
        assert resolve_time_zone() == 'Asia/Tokyo'


class TestTimezoneActivationMiddleware:
    def test_activates_resolved_zone_then_deactivates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from django.http import HttpRequest, HttpResponse
        from django.test import RequestFactory
        from django.utils import timezone as dj_tz

        from anthias_server.lib import timezone as tz_mw

        seen = {}

        def get_response(request: HttpRequest) -> HttpResponse:
            seen['during'] = dj_tz.get_current_timezone_name()
            return HttpResponse('ok')

        # Establish a deterministic baseline (the process default) so the
        # teardown check doesn't assume what settings.TIME_ZONE is — it
        # could legitimately be Europe/Stockholm on some host.
        dj_tz.deactivate()
        baseline = dj_tz.get_current_timezone_name()

        monkeypatch.setattr(
            tz_mw, 'resolve_time_zone', lambda: 'Europe/Stockholm'
        )
        middleware = tz_mw.TimezoneActivationMiddleware(get_response)

        response = middleware(RequestFactory().get('/'))
        assert response.content == b'ok'
        # Active during the request...
        assert seen['during'] == 'Europe/Stockholm'
        # ...and torn back down to the process default afterwards.
        assert dj_tz.get_current_timezone_name() == baseline

    def test_bad_zone_does_not_crash_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from django.http import HttpRequest, HttpResponse
        from django.test import RequestFactory

        from anthias_server.lib import timezone as tz_mw

        def boom() -> str:
            raise ValueError('bad zone')

        def get_response(request: HttpRequest) -> HttpResponse:
            return HttpResponse('ok')

        monkeypatch.setattr(tz_mw, 'resolve_time_zone', boom)
        middleware = tz_mw.TimezoneActivationMiddleware(get_response)

        # Falls back to deactivate() rather than 500-ing.
        response = middleware(RequestFactory().get('/'))
        assert response.content == b'ok'
