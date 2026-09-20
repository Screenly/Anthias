import asyncio
import io
import json
import logging
import os
import sys
import tarfile
import threading
from collections.abc import AsyncGenerator, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from os import getenv, makedirs, path, remove
from typing import Any

from anthias_common.utils import STAGED_UPLOAD_DIR
from anthias_common.version import get_anthias_release

logger = logging.getLogger(__name__)

directories = ['.anthias', 'anthias_assets']

# Tarballs created by older releases used these top-level entry names.
# Recognise them so users can still restore pre-rename backups.
legacy_directories = ['.screenly', 'screenly_assets']

# The runtime is exactly two things: a config directory and a media
# store. Backup and restore work off a *whitelist* of what belongs in
# them — a backup ships only these, and a restore writes only these.
# Anything else that happens to sit under the config dir (SSL keys the
# operator installed, a Caddyfile, the playback-stats log, or a file a
# crafted archive tries to smuggle in) is not runtime state, so it is
# neither shipped nor restored. Whitelisting rather than blacklisting is
# deliberate: we do not try to enumerate every dangerous file (SSH keys
# and the like), we simply refuse everything we do not positively
# recognise as ours.
_CONFIG_DIRS = ('.anthias', '.screenly')
_ASSET_DIRS = ('anthias_assets', 'screenly_assets')

# The files the runtime keeps directly under the config dir: the
# settings file, the metadata database (plus its SQLite sidecars), and
# the default-asset manifest, under both the current and legacy names.
_CONFIG_FILES = frozenset(
    {
        'anthias.conf',
        'anthias.db',
        'anthias.db-wal',
        'anthias.db-shm',
        'anthias.db-journal',
        'default_assets.yml',
        'screenly.conf',
        'screenly.db',
        'screenly.db-wal',
        'screenly.db-shm',
        'screenly.db-journal',
    }
)
# Sub-directories of the config dir that are part of the runtime — the
# django-dbbackup dumps (and their metadata sidecars) live here.
_CONFIG_SUBTREES = ('backups',)


def _is_runtime_member(name: str) -> bool:
    """True if a tar member is part of the Anthias runtime.

    The whitelist both directions run through:

    * the config dir itself, its known files (see ``_CONFIG_FILES``),
      and its runtime sub-trees (see ``_CONFIG_SUBTREES``);
    * the media store and any asset beneath it.

    Everything else — keys, logs, a Caddyfile, an atomic-write sidecar,
    a path a hostile archive invents — is not ours and returns False.
    Path confinement to the extraction root is enforced separately by
    ``_safe_tar_member``.
    """
    parts = name.replace('\\', '/').strip('/').split('/')
    if not parts or parts == ['']:
        return False
    top, rest = parts[0], parts[1:]
    if top in _ASSET_DIRS:
        # The media store holds arbitrary user files; the whole subtree
        # is runtime state.
        return True
    if top in _CONFIG_DIRS:
        if not rest:
            return True  # the config directory entry itself
        if len(rest) == 1 and rest[0] in _CONFIG_FILES:
            return True
        return rest[0] in _CONFIG_SUBTREES
    return False


def _exclude_from_backup(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Filter applied to every member as a backup archive is built.

    Ships only runtime state (see ``_is_runtime_member``); everything
    else under the backed-up directories is dropped. Within the
    whitelisted media store it also drops half-finished uploads:
    ``tar.add`` recurses, and the asset dir holds several kinds of
    in-progress file, each of which can be gigabytes of something nobody
    finished sending — ``.uploads/<id>.part`` from the browser,
    ``<upload_id>.tmp`` from the REST API, and ``.import-<hex>`` (plus
    its ``.part``) from the content importer, which allows 5 GiB. All are
    meaningless once restored, so they only inflate the archive.
    """
    if not _is_runtime_member(member.name):
        return None

    parts = member.name.split('/')
    if parts[0] in _ASSET_DIRS and len(parts) > 1:
        if STAGED_UPLOAD_DIR in parts:
            return None
        leaf = parts[-1]
        if leaf.endswith(('.tmp', '.part')) or leaf.startswith('.import-'):
            return None
    return member


default_archive_name = 'anthias-backup'
static_dir = 'anthias/staticfiles'


def _safe_tar_member(member: tarfile.TarInfo, dest_root: str) -> bool:
    """Validate a TarInfo for safe extraction under dest_root.

    Reject:
      - absolute paths (drive-letter or starts-with-/)
      - any '..' path component (parent traversal)
      - anything that is not runtime state (see ``_is_runtime_member``);
        this whitelist is what keeps keys, logs and stray files out
      - links and special files (symlinks, hardlinks, devices, FIFOs)
      - members that resolve outside dest_root after normalisation

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
    # Whitelist: restore only files that belong to the runtime. Anything
    # else in the archive is skipped, so a crafted backup can neither
    # plant a key nor drop a stray file on the device.
    if not _is_runtime_member(name):
        return False
    if not (member.isfile() or member.isdir()):
        return False
    # Final defence: resolve the destination path and confirm it stays
    # under dest_root. Catches any normalisation gap above.
    target = path.realpath(path.join(dest_root, name))
    root = path.realpath(dest_root)
    return target == root or target.startswith(root + os.sep)


class BackupRecoverError(Exception):
    """Raised when a backup archive cannot be safely recovered."""


class IncompatibleBackupError(BackupRecoverError):
    """Raised when a backup was made by a newer, incompatible version.

    A subclass of ``BackupRecoverError`` so existing ``except`` clauses
    still treat it as a failed restore, but distinct so the recover
    views can surface its specific "upgrade first" message instead of
    the generic "invalid archive" one.
    """


# Metadata member carried at the archive root. It records which version
# of Anthias produced the archive so a restore can refuse a backup from
# a newer release (whose data this system may not understand) and can
# tell that an older backup needs its database migrated up. Kept at the
# root, outside the two runtime directories, so it is never written to
# disk on restore — it is read for the version check and then ignored by
# the extraction whitelist.
BACKUP_MANIFEST_NAME = 'anthias-backup.json'

# Bump only if the archive layout itself changes shape in a way an older
# reader could not parse. It is independent of the schema version below.
BACKUP_FORMAT_VERSION = 1

# The Django app whose migrations define the backup's data schema. The
# schema version is the highest migration number applied for this app —
# monotonic across releases and readable from disk without a database.
_SCHEMA_APP_LABEL = 'anthias_app'


def _current_schema_version() -> int | None:
    """Highest ``anthias_app`` migration number this code carries.

    Read from the migration files on disk (no database connection), so
    it is available both when writing a backup and when validating one.
    Returns ``None`` if the migration graph can't be read; callers treat
    an unknown version as "can't decide", so a lookup failure never
    stamps a wrong number into a backup nor wrongly rejects a restore.
    """
    try:
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        numbers = [
            int(prefix)
            for (app_label, name) in loader.disk_migrations
            if app_label == _SCHEMA_APP_LABEL
            and (prefix := name.split('_', 1)[0]).isdigit()
        ]
        return max(numbers, default=0)
    except Exception:
        logger.exception('Could not determine the backup schema version')
        return None


def _build_manifest() -> dict[str, Any]:
    return {
        'format': BACKUP_FORMAT_VERSION,
        'anthias_version': get_anthias_release(),
        'schema_version': _current_schema_version(),
        'created_at': datetime.now(UTC).isoformat(),
    }


def _add_manifest(tar: tarfile.TarFile) -> None:
    """Write the version manifest into an archive as it is built."""
    payload = json.dumps(_build_manifest(), indent=2).encode('utf-8')
    info = tarfile.TarInfo(BACKUP_MANIFEST_NAME)
    info.size = len(payload)
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(payload))


def _read_manifest(tar: tarfile.TarFile) -> dict[str, Any] | None:
    """Return the parsed manifest, or None for a pre-versioning backup."""
    try:
        member = tar.getmember(BACKUP_MANIFEST_NAME)
    except KeyError:
        return None
    if not member.isfile():
        return None
    fobj = tar.extractfile(member)
    if fobj is None:
        return None
    try:
        data = json.loads(fobj.read().decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _check_backup_compatibility(manifest: dict[str, Any] | None) -> None:
    """Reject a backup this system is too old to restore.

    A backup whose schema is newer than this code's may carry data an
    older Anthias cannot represent, so restoring it would silently
    corrupt state — refuse it and tell the operator to upgrade first.
    An older or equal backup is accepted; its database is migrated up
    afterwards (see ``_upgrade_restored_database``). A pre-versioning
    backup (no manifest) predates every schema we know, so it is treated
    as older and accepted.
    """
    if not manifest:
        return
    backup_schema = manifest.get('schema_version')
    # bool is an int subclass; a boolean here is a malformed manifest,
    # not a schema number.
    if isinstance(backup_schema, bool) or not isinstance(backup_schema, int):
        return
    current = _current_schema_version()
    if current is None:
        # Couldn't read our own schema — don't guess, don't block.
        logger.warning(
            'Skipping backup version check: local schema version unknown'
        )
        return
    if backup_schema > current:
        release = manifest.get('anthias_version') or 'a newer release'
        raise IncompatibleBackupError(
            f'This backup was created by a newer version of Anthias '
            f'({release}, data schema {backup_schema}) than this system '
            f'supports (data schema {current}). Upgrade Anthias to at '
            f'least that version before restoring this backup.'
        )


def _upgrade_restored_database() -> None:
    """Bring a just-restored database up to the current schema.

    An older backup's database is behind this code's migrations; run
    them so the restore is immediately consistent. The startup ``migrate``
    pass is the backstop if this fails, so a failure is logged rather
    than aborting the restore. Skipped under the test environment, where
    there is no real database to migrate.
    """
    if getenv('ENVIRONMENT') == 'test':
        return
    try:
        from django.core.management import call_command
        from django.db import connections

        # recover() rewrote the database file under this worker's cached
        # SQLite connection, which still points at the old file. Drop it
        # so migrate (and everything after) opens a fresh handle on the
        # restored database rather than migrating a stale one.
        connections.close_all()
        call_command('migrate', interactive=False, verbosity=0)
    except Exception:
        logger.exception(
            'Post-restore database migration failed; the next startup '
            'migrate will retry'
        )


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
        datetime.now(UTC).strftime('%Y-%m-%dT%H-%M-%S'),
    )


def stream_backup() -> Generator[bytes]:
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
            with (
                os.fdopen(write_fd, 'wb') as write_file,
                tarfile.open(
                    fileobj=write_file,
                    mode='w|gz',
                    compresslevel=BACKUP_COMPRESSLEVEL,
                ) as tar,
            ):
                _add_manifest(tar)
                for directory in directories:
                    tar.add(
                        path.join(home, directory),
                        arcname=directory,
                        filter=_exclude_from_backup,
                    )
        except BrokenPipeError:
            logger.info('backup download cancelled by the client')
        except Exception as exc:
            logger.exception('backup stream failed')
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


async def astream_backup() -> AsyncGenerator[bytes]:
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
            _add_manifest(tar)
            for directory in directories:
                path_to_dir = path.join(home, directory)
                tar.add(
                    path_to_dir,
                    arcname=directory,
                    filter=_exclude_from_backup,
                )
    except OSError:
        remove(file_path)
        raise

    return archive_name


def recover(file_path: str) -> None:
    home = getenv('HOME')
    if not home:
        logger.error('No HOME variable')
        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    with tarfile.open(file_path, 'r:gz') as tar:
        names = tar.getnames()
        new_present = all(d in names for d in directories)
        legacy_present = all(d in names for d in legacy_directories)
        if not new_present and not legacy_present:
            raise BackupRecoverError('Archive is wrong.')

        # Version guard: refuse a backup from a newer release before we
        # write anything. Raises IncompatibleBackupError, which the
        # recover views surface with its "upgrade first" message.
        _check_backup_compatibility(_read_manifest(tar))

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
            # The manifest is metadata read above, not runtime state;
            # never write it to disk.
            if member.name == BACKUP_MANIFEST_NAME:
                continue
            if not _safe_tar_member(member, home):
                logger.warning(
                    'Skipping unsafe tar member during recover: %r',
                    member.name,
                )
                continue
            tar.extract(member, **extract_kwargs)

    # The archive may hold an older schema; migrate the restored
    # database up to what this system expects.
    _upgrade_restored_database()

    remove(file_path)
