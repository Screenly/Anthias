"""Tests for storage-health detection and its latch.

The module reads four unrelated corners of sysfs and procfs, so most
of what follows builds fake trees and points the module's path
constants at them. The write check is the exception: it runs against
a real tmp directory, because the whole point of it is that it does
real I/O.
"""

from __future__ import annotations

import errno
import json
import os
from typing import Any
from unittest import mock

import pytest

from anthias_common import storage_health


def _mountinfo(data_mount: str, read_only: bool = False) -> str:
    """A realistic container mountinfo.

    The bind mount for the data directory is the shape the container
    actually sees: mount point inside the container, device number of
    the host's SD card partition. The root is deliberately a
    *different* device (8:1) so a test that accidentally resolves to
    ``/`` instead of the data mount fails rather than quietly passing.
    """
    super_options = 'ro,errors=remount-ro' if read_only else 'rw'
    return (
        '25 30 0:24 / /proc rw,nosuid,relatime shared:5 - proc proc rw\n'
        '30 1 8:1 / / rw,relatime - overlay overlay rw\n'
        f'41 30 179:2 /home/pi/.anthias {data_mount} rw,relatime '
        f'- ext4 /dev/mmcblk0p2 {super_options}\n'
        '42 30 179:1 / /boot/firmware rw,relatime - vfat /dev/mmcblk0p1 rw\n'
    )


@pytest.fixture
def fake_redis() -> Any:
    from conftest import _make_fake_redis

    return _make_fake_redis()


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)


@pytest.fixture
def sysfs(tmp_path: Any, monkeypatch: Any) -> Any:
    """A fake sysfs the module's constants are pointed at.

    Returns a builder so each test declares only the parts it cares
    about, and the builder returns the data directory to hand back to
    the module. The layout mirrors a real Pi: ``/sys/dev/block/179:2``
    is a symlink into the device tree, the partition sits inside its
    disk's directory, and ext4 keys its own directory on the partition
    name.

    The data directory is a real, empty subdirectory of ``tmp_path``
    rather than ``tmp_path`` itself, so the fake sysfs files living
    alongside it can't be mistaken for something the write check left
    behind.
    """
    root = tmp_path / 'sys'

    def _build(
        *,
        mountinfo: str | None = None,
        data_mount: str | None = None,
        read_only: bool = False,
        partition: str | None = 'mmcblk0p2',
        disk: str = 'mmcblk0',
        ext4: dict[str, str] | None = None,
        mmc: dict[str, str] | None = None,
        uptime: str = '3600.00 7000.00',
    ) -> str:
        if data_mount is None:
            data_mount = str(tmp_path / 'data')
            os.makedirs(data_mount, exist_ok=True)
        if mountinfo is None:
            mountinfo = _mountinfo(data_mount, read_only=read_only)

        mountinfo_path = tmp_path / 'mountinfo'
        mountinfo_path.write_text(mountinfo)
        monkeypatch.setattr(
            storage_health, 'MOUNTINFO_PATH', str(mountinfo_path)
        )

        uptime_path = tmp_path / 'uptime'
        uptime_path.write_text(uptime)
        monkeypatch.setattr(storage_health, 'UPTIME_PATH', str(uptime_path))

        block = root / 'block' / disk
        (block / 'device').mkdir(parents=True, exist_ok=True)
        for name, value in (mmc or {}).items():
            _write(str(block / 'device' / name), f'{value}\n')

        # The device the mount points at. A partition lives inside its
        # disk's directory and carries a ``partition`` attribute; a
        # whole device has neither, which is how resolve_device tells
        # them apart.
        if partition:
            target = block / partition
            target.mkdir(parents=True, exist_ok=True)
            _write(str(target / 'partition'), '2\n')
        else:
            target = block

        dev_block = root / 'dev' / 'block'
        dev_block.mkdir(parents=True, exist_ok=True)
        link = dev_block / '179:2'
        if not link.exists():
            os.symlink(str(target), str(link))

        if ext4 is not None:
            ext4_dir = root / 'fs' / 'ext4' / (partition or disk)
            ext4_dir.mkdir(parents=True, exist_ok=True)
            for name, value in ext4.items():
                _write(str(ext4_dir / name), f'{value}\n')

        monkeypatch.setattr(
            storage_health, 'SYS_DEV_BLOCK', str(root / 'dev' / 'block')
        )
        monkeypatch.setattr(storage_health, 'SYS_BLOCK', str(root / 'block'))
        monkeypatch.setattr(
            storage_health, 'SYS_FS_EXT4', str(root / 'fs' / 'ext4')
        )
        return data_mount

    return _build


class TestFindMount:
    def test_picks_the_filesystem_backing_the_data_directory(
        self, sysfs: Any
    ) -> None:
        data_dir = sysfs(data_mount='/data/.anthias')

        mount = storage_health.find_mount(data_dir)

        assert mount is not None
        assert mount['mount_point'] == '/data/.anthias'
        assert mount['major_minor'] == '179:2'
        assert mount['fstype'] == 'ext4'

    def test_falls_back_to_the_enclosing_mount(self, sysfs: Any) -> None:
        # A dev install with no bind mount still has to resolve to
        # something, and the root filesystem is the right answer.
        sysfs()

        mount = storage_health.find_mount('/var/lib/somewhere')

        assert mount is not None
        assert mount['mount_point'] == '/'

    def test_longest_prefix_wins(self, sysfs: Any) -> None:
        # /data/.anthias must beat /, not the other way round.
        sysfs(data_mount='/data/.anthias')

        mount = storage_health.find_mount('/data/.anthias/anthias.db')

        assert mount is not None
        assert mount['mount_point'] == '/data/.anthias'

    def test_does_not_match_a_partial_component(self, sysfs: Any) -> None:
        # /data must not appear to contain /database.
        mountinfo = (
            '30 1 179:2 / / rw,relatime - overlay overlay rw\n'
            '41 30 179:2 / /data rw,relatime - ext4 /dev/mmcblk0p2 rw\n'
        )
        sysfs(mountinfo=mountinfo)

        mount = storage_health.find_mount('/database')

        assert mount is not None
        assert mount['mount_point'] == '/'

    def test_missing_mountinfo_is_none(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            storage_health, 'MOUNTINFO_PATH', '/nonexistent/mountinfo'
        )

        assert storage_health.find_mount('/data') is None

    def test_decodes_escaped_mount_points(self, sysfs: Any) -> None:
        mountinfo = (
            '30 1 179:2 / / rw,relatime - overlay overlay rw\n'
            '41 30 179:2 / /mnt/my\\040disk rw,relatime - ext4 /dev/x rw\n'
        )
        sysfs(mountinfo=mountinfo)

        mount = storage_health.find_mount('/mnt/my disk/file')

        assert mount is not None
        assert mount['mount_point'] == '/mnt/my disk'


class TestIsReadOnly:
    def test_writable_filesystem(self) -> None:
        assert (
            storage_health.is_read_only(
                {'mount_options': 'rw,relatime', 'super_options': 'rw'}
            )
            is False
        )

    def test_superblock_read_only_is_caught(self) -> None:
        # This is the failure the whole check exists for. When ext4
        # trips errors=remount-ro it marks the *superblock* read-only
        # while the per-mount options can still read rw, so looking
        # only at field 5 would miss a card that has already stopped
        # accepting writes.
        assert (
            storage_health.is_read_only(
                {
                    'mount_options': 'rw,relatime',
                    'super_options': 'ro,errors=remount-ro',
                }
            )
            is True
        )

    def test_emergency_ro_is_caught(self) -> None:
        # Measured, not guessed. Injecting an error through
        # /sys/fs/ext4/<dev>/trigger_fs_error on a loopback ext4
        # (kernel 7.0) put the filesystem into a state where every
        # write returned EROFS while the mount options still read
        # `rw,relatime` and statvfs's ST_RDONLY stayed clear. This
        # string was the only passive evidence that anything was
        # wrong, so matching only 'ro' silently missed the exact
        # failure this module exists to catch.
        assert (
            storage_health.is_read_only(
                {
                    'mount_options': 'rw,relatime',
                    'super_options': 'rw,errors=remount-ro,emergency_ro',
                }
            )
            is True
        )

    def test_shutdown_is_caught(self) -> None:
        assert (
            storage_health.is_read_only(
                {'mount_options': 'rw', 'super_options': 'rw,shutdown'}
            )
            is True
        )

    def test_per_mount_read_only_is_caught(self) -> None:
        assert (
            storage_health.is_read_only(
                {'mount_options': 'ro,relatime', 'super_options': 'rw'}
            )
            is True
        )

    def test_relatime_is_not_mistaken_for_ro(self) -> None:
        # Substring matching would find "ro" inside "errors=remount-ro"
        # on a perfectly healthy filesystem and warn every device in
        # the fleet.
        assert (
            storage_health.is_read_only(
                {
                    'mount_options': 'rw,relatime',
                    'super_options': 'rw,errors=remount-ro',
                }
            )
            is False
        )


class TestResolveDevice:
    def test_partition_resolves_to_device_and_disk(self, sysfs: Any) -> None:
        sysfs()

        resolved = storage_health.resolve_device('179:2')

        assert resolved['device'] == 'mmcblk0p2'
        assert resolved['disk'] == 'mmcblk0'

    def test_whole_device_is_its_own_disk(self, sysfs: Any) -> None:
        sysfs(partition=None)

        resolved = storage_health.resolve_device('179:2')

        assert resolved['device'] == 'mmcblk0'
        assert resolved['disk'] == 'mmcblk0'

    def test_virtual_filesystem_resolves_to_nothing(self, sysfs: Any) -> None:
        # overlayfs has a device number but no /sys/dev/block entry.
        sysfs()

        resolved = storage_health.resolve_device('0:24')

        assert resolved['device'] is None
        assert resolved['disk'] is None


class TestReadExt4Errors:
    def test_reads_the_superblock_counters(self, sysfs: Any) -> None:
        sysfs(
            ext4={
                'errors_count': '6',
                'first_error_time': '1700000000',
                'last_error_time': '1700003600',
                'last_error_func': 'ext4_find_entry',
                'lifetime_write_kbytes': '4194304',
            }
        )

        stats = storage_health.read_ext4_errors('mmcblk0p2')

        assert stats['supported'] is True
        assert stats['count'] == 6
        assert stats['last_function'] == 'ext4_find_entry'
        assert stats['lifetime_written_kb'] == 4194304
        assert stats['first_time'].startswith('2023-11-14')

    def test_zero_timestamp_is_not_a_date(self, sysfs: Any) -> None:
        # ext4 writes 0 when it has never recorded an error; rendering
        # that as 1970 would look like a very old fault.
        sysfs(ext4={'errors_count': '0', 'last_error_time': '0'})

        stats = storage_health.read_ext4_errors('mmcblk0p2')

        assert stats['count'] == 0
        assert stats['last_time'] is None

    def test_non_ext4_is_unsupported_not_healthy(self, sysfs: Any) -> None:
        # f2fs and btrfs have no equivalent. The write check still
        # covers them, so this degrades rather than going silent.
        sysfs(ext4=None)

        stats = storage_health.read_ext4_errors('mmcblk0p2')

        assert stats['supported'] is False
        assert stats['count'] == 0


class TestReadMediaInfo:
    def test_identifies_an_sd_card(self, sysfs: Any) -> None:
        sysfs(
            mmc={
                'type': 'SD',
                'name': 'SC32G',
                'manfid': '0x000003',
                'date': '03/2019',
            }
        )

        media = storage_health.read_media_info('mmcblk0')

        assert media['kind'] == 'sd'
        assert media['name'] == 'SC32G'
        assert media['manufacturer'] == 'SanDisk'
        assert media['manufactured'] == '03/2019'
        # SD cards report no wear. Claiming 0% would be a lie the UI
        # would then render as good news.
        assert media['wear_pct'] is None

    def test_unknown_manufacturer_falls_back_to_hex(self, sysfs: Any) -> None:
        sysfs(mmc={'type': 'SD', 'manfid': '0x0000ee'})

        media = storage_health.read_media_info('mmcblk0')

        # None, not a placeholder string: no published list carries
        # this id, and "Unknown (0xee)" put filler where the UI
        # expects a company name. The raw id is still reported.
        assert media['manufacturer'] is None
        assert media['manufacturer_id'] == 0xEE

    def test_emmc_ids_are_not_read_off_the_sd_table(self, sysfs: Any) -> None:
        # SD and eMMC manufacturer IDs are different namespaces. 0x03
        # is SanDisk on the SD bus and something else entirely under
        # JEDEC, so sharing one table produced a confident wrong
        # answer -- the exact failure mode this module avoids
        # elsewhere by falling back to hex.
        sysfs(mmc={'type': 'MMC', 'manfid': '0x000003'})

        media = storage_health.read_media_info('mmcblk0')

        assert media['kind'] == 'emmc'
        # 0x03 is SanDisk on the SD bus and Toshiba on eMMC.
        assert media['manufacturer'] == 'Toshiba'

    def test_known_emmc_id_resolves(self, sysfs: Any) -> None:
        # Transcribed verbatim from mmc-utils rather than tidied into
        # a single vendor: upstream lists the ambiguity because the id
        # really is shared, and inventing a cleaner answer here would
        # be the same mistake as the SD/eMMC table merge.
        sysfs(mmc={'type': 'MMC', 'manfid': '0x000015'})

        assert (
            storage_health.read_media_info('mmcblk0')['manufacturer']
            == 'Samsung/SanDisk/LG'
        )

    def test_reads_emmc_wear_registers(self, sysfs: Any) -> None:
        sysfs(
            mmc={
                'type': 'MMC',
                'name': 'DG4008',
                'life_time': '0x02 0x09',
                'pre_eol_info': '0x02',
            }
        )

        media = storage_health.read_media_info('mmcblk0')

        assert media['kind'] == 'emmc'
        # The worse of the two areas wins: a device whose SLC area is
        # spent is at end of life whatever the other number says.
        assert media['wear_pct'] == 90
        assert media['pre_eol'] == 'warning'

    def test_exceeded_life_estimate_clamps_to_100(self, sysfs: Any) -> None:
        sysfs(mmc={'type': 'MMC', 'life_time': '0x0b 0x0b'})

        assert storage_health.read_media_info('mmcblk0')['wear_pct'] == 100

    def test_undefined_life_time_is_none(self, sysfs: Any) -> None:
        # 0x00 means "not defined", not "no wear".
        sysfs(mmc={'type': 'MMC', 'life_time': '0x00 0x00'})

        assert storage_health.read_media_info('mmcblk0')['wear_pct'] is None


class TestSmartMerge:
    """SMART folds into the same wear vocabulary eMMC populates.

    The point of merging rather than adding a parallel field is that
    an SSD and a compute module's soldered eMMC then render through
    one path, so these tests are mostly about the merge refusing to
    report the wrong drive's numbers.
    """

    def _smart(self, **overrides: Any) -> dict[str, Any]:
        fact = {
            'supported': True,
            'device': '/dev/sda',
            'model': 'Crucial CT250MX500SSD1',
            'passed': True,
            'wear_pct': 88,
            'wear_is_exact': False,
            'pre_eol': 'warning',
        }
        fact.update(overrides)
        return fact

    def test_smart_supplies_wear_for_a_disk(self, sysfs: Any) -> None:
        sysfs(disk='sda', partition='sda1')

        media = storage_health.read_media_info('sda', self._smart())

        assert media['kind'] == 'disk'
        assert media['wear_pct'] == 88
        assert media['pre_eol'] == 'warning'
        assert media['name'] == 'Crucial CT250MX500SSD1'

    def test_a_disk_without_a_smart_fact_reports_no_wear(
        self, sysfs: Any
    ) -> None:
        # The viewer may be down, or smartmontools absent. Reporting 0%
        # wear would be a confident wrong answer; None is the truth.
        sysfs(disk='sda', partition='sda1')

        media = storage_health.read_media_info('sda', None)

        assert media['kind'] == 'disk'
        assert media['wear_pct'] is None
        assert media['pre_eol'] is None
        assert media['smart'] is None

    def test_a_fact_for_a_different_drive_is_ignored(self, sysfs: Any) -> None:
        # A box with a boot SSD and a separate data drive would
        # otherwise have one drive's wear reported against the other.
        sysfs(disk='sda', partition='sda1')

        media = storage_health.read_media_info(
            'sda', self._smart(device='/dev/nvme0n1', wear_pct=2)
        )

        assert media['wear_pct'] is None
        assert media['smart'] is None

    def test_an_unsupported_fact_is_ignored(self, sysfs: Any) -> None:
        sysfs(disk='sda', partition='sda1')

        media = storage_health.read_media_info(
            'sda', self._smart(supported=False)
        )

        assert media['wear_pct'] is None
        assert media['smart'] is None

    def test_sd_cards_never_consult_smart(self, sysfs: Any) -> None:
        # An SD card has its own registers and no SMART at all, so a
        # stray fact must not leak into its reading.
        sysfs(mmc={'type': 'SD', 'name': 'SC32G'})

        media = storage_health.read_media_info(
            'mmcblk0', self._smart(device='/dev/mmcblk0')
        )

        assert media['kind'] == 'sd'
        assert media['wear_pct'] is None
        assert media['smart'] is None

    def test_smart_wear_drives_the_status_ladder(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # The whole reason for merging into wear_pct: no new status
        # and no new UI branch, the existing wear path just works.
        data_dir = sysfs(
            disk='sda', partition='sda1', ext4={'errors_count': '0'}
        )
        with mock.patch(
            'anthias_common.storage_health.smart.read',
            return_value=self._smart(),
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )

        assert state['status'] == storage_health.STATUS_WEAR
        assert state['media']['wear_pct'] == 88
        assert storage_health.should_warn(state) is True

    def test_a_failed_self_assessment_warns(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(
            disk='sda', partition='sda1', ext4={'errors_count': '0'}
        )
        with mock.patch(
            'anthias_common.storage_health.smart.read',
            return_value=self._smart(
                passed=False, pre_eol='urgent', wear_pct=None
            ),
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )

        # Nothing is broken yet -- writes still work -- so this is
        # wear, not failing. But it must not read as ok.
        assert state['status'] == storage_health.STATUS_WEAR
        assert state['media']['pre_eol'] == 'urgent'

    def test_get_state_never_blocks_on_smart(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # A page render reads the published fact from Redis; it must
        # never shell out to smartctl itself.
        data_dir = sysfs(
            disk='sda', partition='sda1', ext4={'errors_count': '0'}
        )
        with mock.patch(
            'anthias_common.storage_health.smart.collect'
        ) as collect:
            storage_health.get_state(fake_redis, data_dir)

        collect.assert_not_called()


class TestWriteCheck:
    def test_round_trip_succeeds_on_a_healthy_filesystem(
        self, tmp_path: Any
    ) -> None:
        result = storage_health.run_write_check(str(tmp_path))

        assert result['ok'] is True
        assert result['reason'] is None
        assert result['fsync_ms'] is not None

    def test_leaves_nothing_behind(self, tmp_path: Any) -> None:
        # backup_helper tars the whole of ~/.anthias, so a file left
        # here would ride along in every backup an operator downloads
        # and turn up in debug bundles as noise.
        storage_health.run_write_check(str(tmp_path))
        storage_health.run_write_check(str(tmp_path))

        assert os.listdir(tmp_path) == []

    def test_cleans_up_after_a_corrupt_readback(self, tmp_path: Any) -> None:
        # The failure paths must not litter either, and this is the one
        # that gets far enough to have created the file.
        real_open = open

        def _corrupting_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if str(path).endswith(storage_health.CANARY_FILENAME):
                return real_open(os.devnull, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=_corrupting_open):
            storage_health.run_write_check(str(tmp_path))

        assert os.listdir(tmp_path) == []

    def test_read_only_filesystem_is_classified(self, tmp_path: Any) -> None:
        with mock.patch(
            'os.open', side_effect=OSError(errno.EROFS, 'read-only')
        ):
            result = storage_health.run_write_check(str(tmp_path))

        assert result['ok'] is False
        assert result['reason'] == storage_health.REASON_READ_ONLY
        assert result['errno'] == 'EROFS'

    def test_full_filesystem_is_classified_separately(
        self, tmp_path: Any
    ) -> None:
        # ENOSPC is not a failing card, and conflating the two would
        # send an operator out to buy hardware they don't need.
        with mock.patch(
            'os.open', side_effect=OSError(errno.ENOSPC, 'no space')
        ):
            result = storage_health.run_write_check(str(tmp_path))

        assert result['reason'] == storage_health.REASON_NO_SPACE

    def test_a_missing_data_directory_is_not_a_failure(
        self, tmp_path: Any
    ) -> None:
        # Matters most on Balena, where the resin-data volume starts
        # empty on a first boot: ENOENT classified as a fault would
        # put "this player can't save anything" on a brand-new healthy
        # device.
        result = storage_health.run_write_check(
            str(tmp_path / 'not-created-yet')
        )

        assert result['reason'] == storage_health.REASON_MISSING

    def test_io_error_is_classified(self, tmp_path: Any) -> None:
        with mock.patch(
            'os.open', side_effect=OSError(errno.EIO, 'I/O error')
        ):
            result = storage_health.run_write_check(str(tmp_path))

        assert result['reason'] == storage_health.REASON_IO_ERROR

    def test_corrupted_readback_is_caught(self, tmp_path: Any) -> None:
        # The payload is unique per run precisely so a stale or
        # zero-filled read fails rather than passing.
        real_open = open

        def _corrupting_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if str(path).endswith(storage_health.CANARY_FILENAME):
                return real_open(os.devnull, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=_corrupting_open):
            result = storage_health.run_write_check(str(tmp_path))

        assert result['ok'] is False
        assert result['reason'] == storage_health.REASON_CORRUPT


class TestRecordCheck:
    def test_healthy_device_reports_ok(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'}, mmc={'type': 'SD'})

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['supported'] is True
        assert state['status'] == storage_health.STATUS_OK
        assert storage_health.should_warn(state) is False

    def test_read_only_filesystem_is_failing(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(read_only=True, ext4={'errors_count': '0'})

        state = storage_health.record_check(
            fake_redis,
            data_dir,
            write_check=False,
            boot_id='boot-a',
        )

        assert state['read_only'] is True
        assert state['status'] == storage_health.STATUS_FAILING

    def test_historical_errors_are_milder_than_new_ones(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # errors_count lives in the superblock and survives reboots, so
        # a nonzero baseline on its own is history, not an emergency.
        data_dir = sysfs(
            ext4={'errors_count': '6', 'last_error_time': '1700000000'}
        )

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['errors_count'] == 6
        assert state['errors_new'] == 0
        assert state['status'] == storage_health.STATUS_ERRORS
        assert storage_health.should_warn(state) is True

    def test_errors_arriving_while_we_watch_escalate(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any, monkeypatch: Any
    ) -> None:
        data_dir = sysfs(
            ext4={'errors_count': '6', 'last_error_time': '1700000000'}
        )
        storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        # The card corrupts another block between two samples.
        data_dir = sysfs(
            ext4={'errors_count': '7', 'last_error_time': '1700000000'}
        )
        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['errors_new'] == 1
        assert state['status'] == storage_health.STATUS_FAILING

    def test_an_error_timestamp_inside_this_boot_escalates(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # Catches errors from before celery started, which the
        # baseline alone would miss -- mount-time errors on a bad card
        # land in exactly that window.
        import time

        data_dir = sysfs(
            ext4={
                'errors_count': '1',
                'last_error_time': str(int(time.time()) - 60),
            },
            uptime='3600.00 7000.00',
        )

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['errors_this_boot'] is True
        assert state['status'] == storage_health.STATUS_FAILING

    def test_an_error_predating_this_boot_does_not(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        import time

        data_dir = sysfs(
            ext4={
                'errors_count': '1',
                'last_error_time': str(int(time.time()) - 86400),
            },
            uptime='3600.00 7000.00',
        )

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['errors_this_boot'] is False
        assert state['status'] == storage_health.STATUS_ERRORS

    def test_a_first_boot_before_the_data_dir_exists_reports_ok(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # End-to-end guard for the Balena first-boot window. The whole
        # verdict must stay out of the failure path, not merely carry
        # a different reason string.
        data_dir = sysfs(ext4={'errors_count': '0'})
        missing = os.path.join(data_dir, 'not-created-yet')

        state = storage_health.record_check(
            fake_redis, missing, boot_id='boot-a'
        )

        assert state['write_ok'] is None
        assert state['write_reason'] == storage_health.REASON_MISSING
        assert state['write_failed_since_boot'] is False
        assert state['status'] == storage_health.STATUS_OK
        assert storage_health.should_warn(state) is False

    def test_a_full_filesystem_is_not_a_failing_card(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'})

        with mock.patch.object(
            storage_health,
            'run_write_check',
            return_value={
                'ok': False,
                'reason': storage_health.REASON_NO_SPACE,
                'errno': 'ENOSPC',
                'fsync_ms': None,
                'checked_at': '2026-08-15T00:00:00+00:00',
            },
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )

        assert state['status'] == storage_health.STATUS_FULL

    def test_freeing_space_clears_the_full_verdict(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # ENOSPC must not latch. Otherwise the operator sees "run out
        # of space", deletes assets exactly as instructed, and the
        # banner turns into "replace the memory card" and stays there
        # until reboot -- punishing them for following the advice.
        data_dir = sysfs(ext4={'errors_count': '0'})
        full = {
            'ok': False,
            'reason': storage_health.REASON_NO_SPACE,
            'errno': 'ENOSPC',
            'fsync_ms': None,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=full
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )
        assert state['status'] == storage_health.STATUS_FULL

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['write_failed_since_boot'] is False
        assert state['status'] == storage_health.STATUS_OK

    def test_a_real_write_failure_still_latches(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # The counterpart: EIO is exactly what the latch is for.
        data_dir = sysfs(ext4={'errors_count': '0'})
        failing = {
            'ok': False,
            'reason': storage_health.REASON_IO_ERROR,
            'errno': 'EIO',
            'fsync_ms': None,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=failing
        ):
            storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['write_failed_since_boot'] is True
        assert state['status'] == storage_health.STATUS_FAILING

    def test_an_old_error_is_not_dragged_into_this_boot(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # boot_time is time.time() - uptime, and a Pi has no RTC, so a
        # clock restored behind real time pushes boot_time into the
        # past and can pull errors from previous boots inside it. An
        # error dated an hour into a month-long uptime cannot be one
        # the watcher missed at startup, so it must stay amber
        # "recorded errors" rather than escalating to red.
        import time as _time

        month = 30 * 86400
        # Five days into a month-long uptime: unambiguously outside
        # the startup window, unlike a value right on the boundary.
        old_error = int(_time.time()) - month + (5 * 86400)
        data_dir = sysfs(
            ext4={
                'errors_count': '3',
                'last_error_time': str(old_error),
            },
            uptime=f'{month}.00 {month}.00',
        )

        state = storage_health.record_check(
            fake_redis, data_dir, write_check=False, boot_id='boot-a'
        )

        assert state['errors_new'] == 0
        assert state['errors_this_boot'] is False
        assert state['status'] == storage_health.STATUS_ERRORS

    def test_an_error_inside_the_startup_window_still_escalates(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # The case the signal exists for: a mount-time error the
        # watcher never saw the count rise for, because it landed
        # before the baseline was taken.
        import time as _time

        uptime = 600.0
        data_dir = sysfs(
            ext4={
                'errors_count': '1',
                'last_error_time': str(int(_time.time()) - 300),
            },
            uptime=f'{uptime} {uptime}',
        )

        state = storage_health.record_check(
            fake_redis, data_dir, write_check=False, boot_id='boot-a'
        )

        assert state['errors_new'] == 0
        assert state['errors_this_boot'] is True
        assert state['status'] == storage_health.STATUS_FAILING

    def test_advisory_ata_wear_does_not_warn_on_its_own(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # ATA has no defined wear field, only vendor attributes that
        # count down from 100 by convention. A drive inverting that
        # convention reads as nearly worn out when new, so the figure
        # is displayed but never raises the banner by itself --
        # which is what smart.py's docstring already claimed.
        data_dir = sysfs(
            disk='sda', partition='sda1', ext4={'errors_count': '0'}
        )
        with mock.patch(
            'anthias_common.storage_health.smart.read',
            return_value={
                'supported': True,
                'device': '/dev/sda',
                'passed': True,
                'wear_pct': 99,
                'wear_is_exact': False,
                'wear_is_advisory': True,
                'pre_eol': None,
            },
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )

        assert state['media']['wear_pct'] == 99
        assert state['status'] == storage_health.STATUS_OK

    def test_authoritative_wear_still_warns(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # NVMe percentage_used and eMMC life_time bands are both
        # defined fields, so they must keep working.
        data_dir = sysfs(
            disk='nvme0n1', partition='nvme0n1p1', ext4={'errors_count': '0'}
        )
        with mock.patch(
            'anthias_common.storage_health.smart.read',
            return_value={
                'supported': True,
                'device': '/dev/nvme0n1',
                'passed': True,
                'wear_pct': 92,
                'wear_is_exact': True,
                'wear_is_advisory': False,
                'pre_eol': None,
            },
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, boot_id='boot-a'
            )

        assert state['status'] == storage_health.STATUS_WEAR

    def test_emmc_wear_warns_before_anything_fails(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(
            ext4={'errors_count': '0'},
            mmc={'type': 'MMC', 'life_time': '0x09 0x09'},
        )

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['status'] == storage_health.STATUS_WEAR
        assert storage_health.should_warn(state) is True

    def test_a_write_failure_is_latched_after_recovery(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # Same reasoning as the under-voltage latch: a write that
        # failed and then succeeded is not a card that is fine, it is
        # a card that is starting to go.
        data_dir = sysfs(ext4={'errors_count': '0'})
        failing = {
            'ok': False,
            'reason': storage_health.REASON_IO_ERROR,
            'errno': 'EIO',
            'fsync_ms': None,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=failing
        ):
            storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['write_ok'] is True
        assert state['write_failed_since_boot'] is True
        assert state['write_fail_count'] == 1
        assert state['status'] == storage_health.STATUS_FAILING

    def test_reboot_clears_what_we_observed_but_not_the_superblock(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(
            ext4={'errors_count': '6', 'last_error_time': '1700000000'}
        )
        failing = {
            'ok': False,
            'reason': storage_health.REASON_IO_ERROR,
            'errno': 'EIO',
            'fsync_ms': None,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=failing
        ):
            storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-b'
        )

        # Our own observations reset with the boot id...
        assert state['write_failed_since_boot'] is False
        assert state['write_fail_count'] == 0
        # ...but ext4's record is the card's, not ours, and a card
        # that corrupted data before the reboot is the same card.
        assert state['errors_count'] == 6
        assert state['status'] == storage_health.STATUS_ERRORS

    def test_a_latch_with_no_boot_id_is_never_trusted(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # Comparing None to a stored None matches, so a device that
        # cannot read its boot id would treat last week's latch as
        # current and never reset -- warning about a card that had
        # already been replaced.
        data_dir = sysfs(ext4={'errors_count': '0'})
        fake_redis.set(
            storage_health.REDIS_KEY,
            json.dumps(
                {
                    'boot_id': None,
                    'write_failed_since_boot': True,
                    'write_fail_count': 9,
                }
            ),
        )

        with mock.patch.object(
            storage_health, 'get_boot_id', return_value=None
        ):
            state = storage_health.record_check(
                fake_redis, data_dir, write_check=False
            )

        assert state['write_failed_since_boot'] is False
        assert state['status'] == storage_health.STATUS_OK

    def test_no_boot_id_does_not_persist_a_latch(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # A latch written with no boot id could outlive the boot it
        # describes, and _load_latch would discard it anyway.
        data_dir = sysfs(ext4={'errors_count': '0'})

        with mock.patch.object(
            storage_health, 'get_boot_id', return_value=None
        ):
            storage_health.record_check(
                fake_redis, data_dir, write_check=False
            )

        assert fake_redis.get(storage_health.REDIS_KEY) is None

    def test_an_unchanged_latch_is_not_rewritten(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # Redis persists to the card. Sampling every 60s with an
        # unconditional SET is ~1,400 fsynced writes a day onto the
        # storage of a healthy device -- self-defeating in a feature
        # whose whole point is avoiding card wear.
        data_dir = sysfs(ext4={'errors_count': '0'})
        storage_health.record_check(
            fake_redis, data_dir, write_check=False, boot_id='boot-a'
        )
        writes_before = fake_redis.set.call_count

        for _ in range(5):
            storage_health.record_check(
                fake_redis, data_dir, write_check=False, boot_id='boot-a'
            )

        assert fake_redis.set.call_count == writes_before

    def test_a_changed_latch_is_still_written(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'})
        storage_health.record_check(
            fake_redis, data_dir, write_check=False, boot_id='boot-a'
        )
        writes_before = fake_redis.set.call_count

        failing = {
            'ok': False,
            'reason': storage_health.REASON_IO_ERROR,
            'errno': 'EIO',
            'fsync_ms': None,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=failing
        ):
            storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        assert fake_redis.set.call_count > writes_before

    def test_corrupt_latch_is_discarded(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'})
        fake_redis.set(storage_health.REDIS_KEY, 'not json')

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['status'] == storage_health.STATUS_OK

    def test_boot_id_is_persisted(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '3'})

        storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        stored = json.loads(fake_redis.get(storage_health.REDIS_KEY))
        assert stored['boot_id'] == 'boot-a'
        assert stored['errors_baseline'] == 3

    def test_redis_failure_still_returns_a_verdict(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'})
        fake_redis.set.side_effect = RuntimeError('redis down')

        state = storage_health.record_check(
            fake_redis, data_dir, boot_id='boot-a'
        )

        assert state['status'] == storage_health.STATUS_OK


class TestGetState:
    def test_unresolvable_filesystem_is_unknown_not_healthy(
        self, monkeypatch: Any, fake_redis: Any
    ) -> None:
        monkeypatch.setattr(
            storage_health, 'MOUNTINFO_PATH', '/nonexistent/mountinfo'
        )

        state = storage_health.get_state(fake_redis, '/data/.anthias')

        assert state['supported'] is False
        assert state['status'] == storage_health.STATUS_UNKNOWN
        assert storage_health.should_warn(state) is False

    def test_never_writes_to_the_card(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any
    ) -> None:
        # A page render must not be able to block on fsync against a
        # card that is already struggling, which is exactly when
        # someone is loading the page.
        data_dir = sysfs(ext4={'errors_count': '0'})

        with mock.patch.object(storage_health, 'run_write_check') as check:
            storage_health.get_state(fake_redis, data_dir)

        check.assert_not_called()
        assert os.listdir(data_dir) == []

    def test_reflects_the_latch_written_by_the_watcher(
        self, sysfs: Any, tmp_path: Any, fake_redis: Any, monkeypatch: Any
    ) -> None:
        data_dir = sysfs(ext4={'errors_count': '0'})
        monkeypatch.setattr(storage_health, 'get_boot_id', lambda: 'boot-a')
        failing = {
            'ok': False,
            'reason': storage_health.REASON_IO_ERROR,
            'errno': 'EIO',
            'fsync_ms': 4200.0,
            'checked_at': '2026-08-15T00:00:00+00:00',
        }
        with mock.patch.object(
            storage_health, 'run_write_check', return_value=failing
        ):
            storage_health.record_check(fake_redis, data_dir, boot_id='boot-a')

        state = storage_health.get_state(fake_redis, data_dir)

        assert state['write_ok'] is False
        assert state['write_reason'] == storage_health.REASON_IO_ERROR
        assert state['fsync_ms'] == 4200.0
        assert state['status'] == storage_health.STATUS_FAILING
