import logging
import os
import sys
import tarfile
import threading
from collections.abc import Iterator
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


def stream_backup() -> Iterator[bytes]:
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
    stops — no orphaned thread keeps taring.
    """
    home = getenv('HOME') or ''
    read_fd, write_fd = os.pipe()

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
        except OSError:
            logging.exception('backup stream failed')

    producer = threading.Thread(
        target=produce, name='backup-stream', daemon=True
    )
    producer.start()
    try:
        with os.fdopen(read_fd, 'rb') as read_file:
            while chunk := read_file.read(_STREAM_CHUNK_BYTES):
                yield chunk
    finally:
        producer.join(timeout=5)


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
