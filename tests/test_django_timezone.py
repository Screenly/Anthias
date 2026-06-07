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

from anthias_server.django_project.settings import get_host_time_zone


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
