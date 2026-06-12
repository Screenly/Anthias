import asyncio
import logging
import os
import sys
import tarfile
import threading
from collections.abc import AsyncGenerator, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os import getenv, makedirs, path, remove
from typing import Any

directories = ['.anthias', 'anthias_assets']
# Tarballs created by older releases used these top-level entry names.
# Recognise them so users can still restore pre-rename backups.
legacy_directories = ['.screenly', 'screenly_assets']
allowed_top_level = set(directories) | set(legacy_directories)
default_archive_name = 'anthias-backup'
static_dir = 'anthias/staticfiles'


def _safe_tar_member(member: tarfile.TarInfo, dest_root: str) -> bool:
    """Validate a TarInfo for safe extraction under dest_root.

    Reject:
      - absolute paths (drive-letter or starts-with-/)
      - any '..' path component (parent traversal)
      - links and special files (symlinks, hardlinks, devices, FIFOs)
      - members that resolve outside dest_root after normalisation
      - members not under one of our expected top-level directories

    Returning False from here causes the extractor to skip the member
    rather than raise — partial recovery is preferable to bailing out
    on the first weird entry, but the calling code logs a warning so
    silent skips are visible.
    """
    name = member.name
    if not name or name.startswith('/') or os.path.isabs(name):
        return False
    parts = name.replace('\\', '/').split('/')
    if any(p in ('', '..') for p in parts):
        return False
    if parts[0] not in allowed_top_level:
        return False
    if not (member.isfile() or member.isdir()):
        return False
    # Final defence: resolve the destination path and confirm it stays
    # under dest_root. Catches any normalisation gap above.
    target = path.realpath(path.join(dest_root, name))
    root = path.realpath(dest_root)
    if not (target == root or target.startswith(root + os.sep)):
        return False
    return True


class BackupRecoverError(Exception):
    """Raised when a backup archive cannot be safely recovered."""


# gzip level for backup archives. The bulk of a backup is video/image
# assets that are already compressed, so the default level 9 burns
# minutes of single-core CPU on an SBC for ~no size win — measured
# 98 s for 355 MB on a Pi 4 (~3.6 MB/s); a multi-GB library on a Pi 3
# runs well past the browser's request timeout (issue #2987, the
# "Get backup never downloads" report). Level 1 is ~4-5x faster and
# within a couple of percent on size for this content mix.
BACKUP_COMPRESSLEVEL = 1

# Chunk size for stream_backup(). 64 KiB matches the pipe capacity on
# Linux so the tar producer thread and the HTTP writer interleave
# without either side stalling on tiny reads.
_STREAM_CHUNK_BYTES = 64 * 1024


def backup_archive_name(name: str = default_archive_name) -> str:
    return '{}-{}.tar.gz'.format(
        name if name else default_archive_name,
        datetime.now().strftime('%Y-%m-%dT%H-%M-%S'),
    )


def stream_backup() -> Generator[bytes, None, None]:
    """Yield a backup tar.gz as it is being built.

    The download path used to write the whole archive to disk before
    sending the first byte. tar+gzip of a multi-GB asset library takes
    minutes on an SBC, and a browser kills a request that has produced
    no response bytes for ~5 minutes — so "Get backup" simply never
    completed on devices with a real content library (issue #2987).
    Streaming starts the response immediately, keeps bytes flowing for
    the whole build, and as a bonus never needs staging space on the
    (often nearly full) SD card.

    A producer thread feeds ``tarfile`` through a pipe; the generator
    reads the other end. A consumer that disconnects mid-download
    closes the read end, the producer hits ``BrokenPipeError`` and
    stops — no orphaned thread keeps taring. A producer failure
    (missing directory, unreadable file) is re-raised here once the
    stream drains, so the response aborts mid-transfer instead of
    completing 200 with a silently truncated archive.
    """
    home = getenv('HOME') or ''
    read_fd, write_fd = os.pipe()
    produce_error: list[BaseException] = []

    def produce() -> None:
        try:
            with os.fdopen(write_fd, 'wb') as write_file:
                with tarfile.open(
                    fileobj=write_file,
                    mode='w|gz',
                    compresslevel=BACKUP_COMPRESSLEVEL,
                ) as tar:
                    for directory in directories:
                        tar.add(path.join(home, directory), arcname=directory)
        except BrokenPipeError:
            logging.info('backup download cancelled by the client')
        except Exception as exc:
            logging.exception('backup stream failed')
            produce_error.append(exc)

    producer = threading.Thread(
        target=produce, name='backup-stream', daemon=True
    )
    producer.start()
    drained = False
    try:
        with os.fdopen(read_fd, 'rb') as read_file:
            while chunk := read_file.read(_STREAM_CHUNK_BYTES):
                yield chunk
        drained = True
    finally:
        producer.join(timeout=5)
        # Only surface producer failures on the drained path: a
        # GeneratorExit (client went away) shouldn't morph into a
        # spurious error.
        if drained and produce_error:
            raise produce_error[0]


async def astream_backup() -> AsyncGenerator[bytes, None]:
    """Async front-end to stream_backup() for the ASGI download view.

    StreamingHttpResponse only *streams* an asynchronous iterator under
    ASGI. Handed a synchronous generator, Django's __aiter__ falls back
    to ``await sync_to_async(list)(...)``, which drains the generator
    whole — i.e. builds the entire archive (and buffers every chunk in
    a RAM list) before the first response byte goes out. That silently
    reintroduces the exact 0-bytes-then-timeout failure stream_backup()
    was written to fix, and risks OOM on a 1 GB Pi with a multi-GB
    library (issue #3073). Driving the sync generator one chunk at a
    time off the event loop keeps bytes flowing as the tar is built and
    the footprint flat.

    A single-worker executor serialises every touch of the sync
    generator — both ``next()`` and the cleanup ``close()`` — onto one
    thread. They therefore can never overlap: if the client disconnects
    mid-``next()``, the queued ``close()`` runs only after that
    ``next()`` returns, so we avoid ``ValueError: generator already
    executing`` and the leaked producer thread that a cross-thread
    close would cause. A dedicated executor (rather than Django's
    shared sync pool) also keeps the long blocking pipe read from
    wedging unrelated sync work.
    """
    loop = asyncio.get_running_loop()
    gen = stream_backup()
    executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='backup-stream-reader'
    )

    def next_chunk() -> bytes | None:
        # next(gen, None) rather than bare next() so exhaustion returns
        # a sentinel instead of raising StopIteration, which can't
        # cross the executor boundary cleanly. stream_backup() only ever
        # yields non-empty bytes, so None is an unambiguous end marker.
        return next(gen, None)

    try:
        while True:
            chunk = await loop.run_in_executor(executor, next_chunk)
            if chunk is None:
                break
            yield chunk
    finally:
        # On client disconnect Django aclose()s this generator. Closing
        # the sync generator throws GeneratorExit into stream_backup at
        # its yield, so its own finally joins the producer thread and
        # closes the pipe (the producer's next write then hits
        # BrokenPipeError and exits) — nothing is left taring.
        try:
            await loop.run_in_executor(executor, gen.close)
        finally:
            executor.shutdown(wait=False)


def create_backup(name: str = default_archive_name) -> str:
    home = getenv('HOME') or ''
    archive_name = backup_archive_name(name)
    file_path = path.join(home, static_dir, archive_name)

    if not path.exists(path.join(home, static_dir)):
        makedirs(path.join(home, static_dir), exist_ok=True)

    if path.isfile(file_path):
        remove(file_path)

    try:
        with tarfile.open(
            file_path, 'w:gz', compresslevel=BACKUP_COMPRESSLEVEL
        ) as tar:
            for directory in directories:
                path_to_dir = path.join(home, directory)
                tar.add(path_to_dir, arcname=directory)
    except IOError as e:
        remove(file_path)
        raise e

    return archive_name


def recover(file_path: str) -> None:
    home = getenv('HOME')
    if not home:
        logging.error('No HOME variable')
        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
        new_present = all(d in names for d in directories)
        legacy_present = all(d in names for d in legacy_directories)
        if not new_present and not legacy_present:
            raise BackupRecoverError('Archive is wrong.')

        # Manually iterate so each member is validated before any
        # filesystem write. Avoids tarfile.extractall's older
        # path-traversal vulnerabilities (Zip Slip / CVE-2007-4559).
        # If running on Python with PEP-706 extraction filters
        # (3.11.4+/3.12+), pass `filter='data'` for belt-and-suspenders
        # protection; older interpreters fall back to our own
        # validation only.
        extract_kwargs: dict[str, Any] = {'path': home}
        if hasattr(tarfile, 'data_filter'):
            extract_kwargs['filter'] = 'data'
        for member in tar.getmembers():
            if not _safe_tar_member(member, home):
                logging.warning(
                    'Skipping unsafe tar member during recover: %r',
                    member.name,
                )
                continue
            tar.extract(member, **extract_kwargs)

    remove(file_path)
