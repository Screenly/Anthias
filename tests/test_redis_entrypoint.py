"""A damaged append-only file must not keep Redis down for good.

A power cut while Redis appends to its incremental AOF can leave a bad
record mid-file. redis-server treats that as fatal at startup, and
`restart: always` puts it straight back into the same failure, so the
player loses its broker, its channel layer and the viewer command bus
until someone repairs the file by hand. A sweep of fleet logs found
players stuck in exactly that loop on 2026.11.2.

docker/redis/entrypoint.sh runs before redis-server and repairs the
file. These tests drive it against real on-disk layouts with the real
redis-check-aof / redis-check-rdb (from redis-tools, installed in the
test image), and hand it `true` in place of redis-server, so no server
is started.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / 'docker' / 'redis' / 'entrypoint.sh'
DOCKERFILE = REPO_ROOT / 'docker' / 'Dockerfile.redis.j2'

requires_redis_tools = pytest.mark.skipif(
    shutil.which('redis-check-aof') is None
    or shutil.which('redis-check-rdb') is None,
    reason='redis-tools is not installed',
)

MANIFEST = 'appendonly.aof.manifest'


def _resp(*parts: bytes) -> bytes:
    out = b'*%d\r\n' % len(parts)
    for part in parts:
        out += b'$%d\r\n%s\r\n' % (len(part), part)
    return out


def _rdb(key: bytes, value: bytes) -> bytes:
    """A minimal valid RDB holding one string key.

    The trailing eight zero bytes mean "checksum disabled", which both
    redis-check-rdb and redis-server accept.
    """
    return (
        b'REDIS0009'
        + b'\xfe\x00'  # SELECTDB 0
        + b'\xfb\x01\x00'  # RESIZEDB: 1 key, 0 expires
        + b'\x00'  # string type
        + bytes([len(key)])
        + key
        + bytes([len(value)])
        + value
        + b'\xff'
        + b'\x00' * 8
    )


def _write_aof(data_dir: Path, base: bytes, incr: bytes) -> Path:
    aof_dir = data_dir / 'appendonlydir'
    aof_dir.mkdir(parents=True)
    (aof_dir / 'appendonly.aof.1.base.aof').write_bytes(base)
    (aof_dir / 'appendonly.aof.1.incr.aof').write_bytes(incr)
    (aof_dir / MANIFEST).write_text(
        'file appendonly.aof.1.base.aof seq 1 type b\n'
        'file appendonly.aof.1.incr.aof seq 1 type i\n'
    )
    return aof_dir


def _run(data_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['sh', str(ENTRYPOINT), 'true', '--dir', str(data_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _aof_is_valid(aof_dir: Path) -> bool:
    result = subprocess.run(
        ['redis-check-aof', str(aof_dir / MANIFEST)],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result.returncode == 0


DEVICE_ID = _resp(b'SET', b'device_id', b'dev42')
LATER = _resp(b'SET', b'later', b'1')


@requires_redis_tools
def test_a_healthy_aof_is_left_alone(tmp_path: Path) -> None:
    aof_dir = _write_aof(tmp_path, DEVICE_ID, LATER)
    before = (aof_dir / 'appendonly.aof.1.incr.aof').read_bytes()

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ''
    assert (aof_dir / 'appendonly.aof.1.incr.aof').read_bytes() == before


@requires_redis_tools
def test_a_bad_record_mid_file_is_truncated_away(tmp_path: Path) -> None:
    """The field failure: a bad record in the middle of the incr AOF.

    Everything before the bad record has to survive the repair, which
    is the point of repairing rather than discarding.
    """
    incr = LATER + b'\xff' * 32 + _resp(b'SET', b'lost', b'1')
    aof_dir = _write_aof(tmp_path, b'', incr)
    assert not _aof_is_valid(aof_dir)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert 'repaired the append-only file' in result.stdout
    assert _aof_is_valid(aof_dir)
    assert (aof_dir / 'appendonly.aof.1.incr.aof').read_bytes() == LATER
    assert not (tmp_path / 'appendonlydir.corrupt').exists()


@requires_redis_tools
def test_an_unrepairable_aof_is_rebuilt_from_the_snapshot(
    tmp_path: Path,
) -> None:
    """A damaged base file cannot be truncated into shape.

    Redis 7+ with appendonly enabled ignores dump.rdb when the AOF
    directory is missing, so moving the AOF aside alone would start an
    empty dataset and lose the device_id that the snapshot still holds.
    """
    snapshot = _rdb(b'device_id', b'dev42')
    (tmp_path / 'dump.rdb').write_bytes(snapshot)
    _write_aof(tmp_path, b'REDIS0009\xff\xff\xff\xff', LATER)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert 'rebuilt the append-only file' in result.stdout
    aof_dir = tmp_path / 'appendonlydir'
    assert _aof_is_valid(aof_dir)
    assert (aof_dir / 'appendonly.aof.1.base.rdb').read_bytes() == snapshot
    assert (tmp_path / 'appendonlydir.corrupt' / MANIFEST).exists()


@requires_redis_tools
def test_without_a_snapshot_redis_still_gets_to_start(
    tmp_path: Path,
) -> None:
    _write_aof(tmp_path, b'REDIS0009\xff\xff\xff\xff', LATER)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert 'starting with an empty dataset' in result.stdout
    assert not (tmp_path / 'appendonlydir').exists()
    assert (tmp_path / 'appendonlydir.corrupt').is_dir()


@requires_redis_tools
def test_only_the_latest_corrupt_copy_is_kept(tmp_path: Path) -> None:
    """Repeated failures must not pile copies up on a small card."""
    (tmp_path / 'appendonlydir.corrupt').mkdir()
    (tmp_path / 'appendonlydir.corrupt' / 'stale').write_text('old')
    _write_aof(tmp_path, b'REDIS0009\xff\xff\xff\xff', LATER)

    _run(tmp_path)

    assert not (tmp_path / 'appendonlydir.corrupt' / 'stale').exists()
    assert (tmp_path / 'appendonlydir.corrupt' / MANIFEST).exists()


@requires_redis_tools
@pytest.mark.parametrize('partial_seed', [False, True])
def test_snapshot_recovery_resumes_after_a_power_cut(
    tmp_path: Path, partial_seed: bool
) -> None:
    snapshot = _rdb(b'device_id', b'dev42')
    (tmp_path / 'dump.rdb').write_bytes(snapshot)
    aof_dir = _write_aof(tmp_path, b'REDIS0009\xff\xff\xff\xff', LATER)
    aof_dir.rename(tmp_path / 'appendonlydir.corrupt')
    if partial_seed:
        aof_dir.mkdir()
        (aof_dir / 'appendonly.aof.1.base.rdb').write_bytes(b'REDIS0009')
        (aof_dir / f'{MANIFEST}.recovery').write_text('file appendonly')

    result = _run(tmp_path)

    assert result.returncode == 0
    assert _aof_is_valid(aof_dir)
    assert (aof_dir / 'appendonly.aof.1.base.rdb').read_bytes() == snapshot
    assert (tmp_path / 'appendonlydir.corrupt' / MANIFEST).exists()
    assert not (aof_dir / f'{MANIFEST}.recovery').exists()


def test_a_first_start_with_no_aof_is_a_no_op(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ''
    assert list(tmp_path.iterdir()) == []


def test_the_image_runs_the_entrypoint_before_redis() -> None:
    source = DOCKERFILE.read_text()

    copied = re.search(
        r'^COPY .*docker/redis/entrypoint\.sh (\S+)$',
        source,
        re.MULTILINE,
    )
    assert copied is not None, 'the entrypoint is not copied into the image'
    assert re.search(
        rf'^ENTRYPOINT \["{re.escape(copied.group(1))}"\]$',
        source,
        re.MULTILINE,
    ), 'the copied entrypoint is not the ENTRYPOINT'
    assert os.access(ENTRYPOINT, os.X_OK)
