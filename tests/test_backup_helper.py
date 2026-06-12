import asyncio
import os
import shutil
import tarfile
import tempfile
import threading
from collections.abc import Iterator
from datetime import datetime
from os import path
from unittest import mock

import pytest
from django.http import StreamingHttpResponse

from anthias_server.lib.backup_helper import (
    astream_backup,
    backup_archive_name,
    create_backup,
    recover,
    static_dir,
    stream_backup,
)


@pytest.fixture
def backup_home() -> Iterator[str]:
    """Exercises create_backup() / recover() under a temporary $HOME so a
    developer running the test on a real workstation never has their
    ~/anthias checkout or ~/.anthias config wiped by tearDown's
    rmtree."""
    tmp_home = tempfile.mkdtemp(prefix='anthias-backup-test-')
    # Populate the layout create_backup() expects to tar up so the
    # call has something to read.
    os.makedirs(path.join(tmp_home, '.anthias'))
    os.makedirs(path.join(tmp_home, 'anthias_assets'))

    home_patch = mock.patch.dict(os.environ, {'HOME': tmp_home})
    home_patch.start()

    assert not path.isdir(path.join(tmp_home, static_dir))

    try:
        yield tmp_home
    finally:
        home_patch.stop()
        shutil.rmtree(tmp_home, ignore_errors=True)


def test_get_backup_name(backup_home: str) -> None:
    dt = datetime(2016, 7, 19, 12, 42, 12)
    expected_archive_name = 'anthias-backup-2016-07-19T12-42-12.tar.gz'
    with mock.patch(
        'anthias_server.lib.backup_helper.datetime'
    ) as mock_datetime:
        mock_datetime.now.return_value = dt
        archive_name = create_backup()
        assert archive_name == expected_archive_name


def test_recover(backup_home: str) -> None:
    archive_name = create_backup()
    file_path = path.join(backup_home, static_dir, archive_name)
    assert path.isfile(file_path)
    recover(file_path)
    assert not path.isfile(file_path)


def test_backup_archive_name_falls_back_on_empty_name() -> None:
    dt = datetime(2016, 7, 19, 12, 42, 12)
    with mock.patch(
        'anthias_server.lib.backup_helper.datetime'
    ) as mock_datetime:
        mock_datetime.now.return_value = dt
        assert (
            backup_archive_name('')
            == 'anthias-backup-2016-07-19T12-42-12.tar.gz'
        )
        assert (
            backup_archive_name('lobby') == 'lobby-2016-07-19T12-42-12.tar.gz'
        )


def test_stream_backup_round_trips_through_recover(
    backup_home: str,
) -> None:
    # The settings page download streams the archive as it is built
    # (issue #2987: the staged-file path produced no response bytes
    # for minutes and browsers gave up). The streamed bytes must be a
    # well-formed tar.gz that recover() accepts unchanged.
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    chunks = list(stream_backup())
    assert chunks

    os.makedirs(path.join(backup_home, static_dir), exist_ok=True)
    file_path = path.join(backup_home, static_dir, 'streamed.tar.gz')
    with open(file_path, 'wb') as f:
        f.write(b''.join(chunks))

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
    assert '.anthias' in names
    assert 'anthias_assets' in names
    assert '.anthias/anthias.conf' in names

    os.remove(marker)
    recover(file_path)
    assert path.isfile(marker)


def test_stream_backup_stops_when_consumer_disconnects(
    backup_home: str,
) -> None:
    # A closed browser connection must not leave the producer thread
    # taring forever — the generator's pipe close propagates as
    # BrokenPipeError and the thread exits.
    stream = stream_backup()
    assert next(stream)
    stream.close()  # GeneratorExit → read end closed
    main_thread = threading.main_thread()
    for thread in threading.enumerate():
        if thread.name == 'backup-stream' and thread is not main_thread:
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_astream_backup_response_streams_under_asgi(
    backup_home: str,
) -> None:
    # Regression for issue #3073. StreamingHttpResponse only streams an
    # *asynchronous* iterator under ASGI; handed a sync generator,
    # Django's __aiter__ does `await sync_to_async(list)(...)` — it
    # builds the whole archive in RAM before the first byte, which
    # reproduced the original 0-bytes-then-timeout failure. The download
    # view must wrap the producer in astream_backup() so Django takes
    # its real streaming path (is_async == True) and round-trips back
    # through recover() unchanged.
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    response = StreamingHttpResponse(
        astream_backup(), content_type='application/x-tgz'
    )
    # The crux of the fix: a sync generator would leave this False and
    # send Django down the list()-buffering branch.
    assert response.is_async is True

    async def drain() -> list[bytes]:
        # aiter(response) is exactly what Django's ASGI handler consumes.
        return [part async for part in aiter(response)]

    chunks = asyncio.run(drain())
    assert chunks

    os.makedirs(path.join(backup_home, static_dir), exist_ok=True)
    file_path = path.join(backup_home, static_dir, 'astreamed.tar.gz')
    with open(file_path, 'wb') as out_file:
        out_file.write(b''.join(chunks))

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
    assert '.anthias/anthias.conf' in names

    os.remove(marker)
    recover(file_path)
    assert path.isfile(marker)


def test_astream_backup_stops_producer_when_consumer_disconnects(
    backup_home: str,
) -> None:
    # A client that disconnects mid-download makes Django aclose() the
    # async generator. Cleanup must stop the producer thread (and not
    # raise) — a cross-thread close racing an in-flight next() would
    # leave it taring forever (PR #3074 review).
    marker = path.join(backup_home, '.anthias', 'anthias.conf')
    with open(marker, 'w') as f:
        f.write('[viewer]\n')

    async def take_one_then_disconnect() -> None:
        agen = astream_backup()
        first = await agen.__anext__()
        assert first
        await agen.aclose()  # GeneratorExit cleanup path

    asyncio.run(take_one_then_disconnect())

    main_thread = threading.main_thread()
    for thread in threading.enumerate():
        if thread.name == 'backup-stream' and thread is not main_thread:
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
def legacy_home() -> Iterator[str]:
    """Backups produced by pre-rename releases used `.screenly` and
    `screenly_assets` as top-level archive entries. recover() must keep
    accepting them so users can still restore those backups."""
    tmp_home = tempfile.mkdtemp(prefix='anthias-backup-legacy-test-')
    try:
        yield tmp_home
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


def _build_legacy_tarball(tmp_home: str) -> str:
    # Stage the legacy layout in a scratch dir, then tar it up with
    # top-level `.screenly/` and `screenly_assets/` arcnames.
    scratch = tempfile.mkdtemp(prefix='anthias-backup-stage-')
    try:
        os.makedirs(path.join(scratch, '.screenly'))
        os.makedirs(path.join(scratch, 'screenly_assets'))
        with open(path.join(scratch, '.screenly', 'screenly.conf'), 'w') as f:
            f.write('[main]\nconfigdir = .screenly\n')
        with open(path.join(scratch, 'screenly_assets', 'a.mp4'), 'wb') as f:
            f.write(b'video-stub')

        archive = path.join(tmp_home, 'legacy-backup.tar.gz')
        # Write mode: building a fixture tarball, not extracting it.
        # arcnames are hardcoded test inputs, so no path-traversal
        # surface. NOSONAR(python:S5042)
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(
                path.join(scratch, '.screenly'),
                arcname='.screenly',
            )
            tar.add(
                path.join(scratch, 'screenly_assets'),
                arcname='screenly_assets',
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return archive


def test_recover_accepts_legacy_archive(legacy_home: str) -> None:
    archive = _build_legacy_tarball(legacy_home)

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        recover(archive)

    # Archive removed (recover() unlinks on success).
    assert not path.isfile(archive)
    # Legacy entries restored under the patched HOME.
    assert path.isfile(path.join(legacy_home, '.screenly', 'screenly.conf'))
    assert path.isfile(path.join(legacy_home, 'screenly_assets', 'a.mp4'))


def test_recover_rejects_unrelated_archive(legacy_home: str) -> None:
    archive = path.join(legacy_home, 'random.tar.gz')
    scratch = tempfile.mkdtemp(prefix='anthias-backup-bogus-')
    try:
        os.makedirs(path.join(scratch, 'unrelated'))
        # Write mode: building a fixture tarball, not extracting it.
        # NOSONAR(python:S5042)
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(
                path.join(scratch, 'unrelated'),
                arcname='unrelated',
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        with pytest.raises(Exception):
            recover(archive)


def test_recover_skips_path_traversal_member(legacy_home: str) -> None:
    """A malicious tarball with a `..` member must not write outside
    $HOME. The required top-level entries are still present, so
    recover() proceeds, but the unsafe member should be skipped."""
    archive = path.join(legacy_home, 'malicious.tar.gz')
    scratch = tempfile.mkdtemp(prefix='anthias-backup-mal-')
    try:
        os.makedirs(path.join(scratch, '.anthias'))
        os.makedirs(path.join(scratch, 'anthias_assets'))
        with open(path.join(scratch, '.anthias', 'anthias.conf'), 'w') as f:
            f.write('[main]\n')
        payload = path.join(scratch, 'evil.txt')
        with open(payload, 'wb') as f:
            f.write(b'pwned')

        # NOSONAR(python:S5042) — fixture builder, write mode.
        with tarfile.open(archive, 'w:gz') as tar:  # NOSONAR
            tar.add(path.join(scratch, '.anthias'), arcname='.anthias')
            tar.add(
                path.join(scratch, 'anthias_assets'),
                arcname='anthias_assets',
            )
            # The hostile member: a relative escape attempt that
            # would land at $HOME/../evil.txt under naive extraction.
            tar.add(payload, arcname='../evil.txt')
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    with mock.patch.dict(os.environ, {'HOME': legacy_home}):
        recover(archive)

    # Legit member extracted; hostile one skipped.
    assert path.isfile(path.join(legacy_home, '.anthias', 'anthias.conf'))
    parent_of_home = path.dirname(legacy_home)
    assert not path.exists(path.join(parent_of_home, 'evil.txt'))
