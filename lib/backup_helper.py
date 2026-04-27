import logging
import os
import sys
import tarfile
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


def create_backup(name: str = default_archive_name) -> str:
    home = getenv('HOME') or ''
    archive_name = '{}-{}.tar.gz'.format(
        name if name else default_archive_name,
        datetime.now().strftime('%Y-%m-%dT%H-%M-%S'),
    )
    file_path = path.join(home, static_dir, archive_name)

    if not path.exists(path.join(home, static_dir)):
        makedirs(path.join(home, static_dir), exist_ok=True)

    if path.isfile(file_path):
        remove(file_path)

    try:
        with tarfile.open(file_path, 'w:gz') as tar:
            for directory in directories:
                path_to_dir = path.join(home, directory)
                tar.add(path_to_dir, arcname=directory)
    except IOError as e:
        remove(file_path)
        raise e

    return archive_name


def _is_within_directory(directory: str, target: str) -> bool:
    abs_directory = path.realpath(directory)
    abs_target = path.realpath(target)
    return path.commonpath([abs_directory, abs_target]) == abs_directory


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract a tar archive guarding against unsafe members.

    `tarfile.extractall` will happily follow `../` segments, absolute
    paths, and special members (symlinks, device nodes, FIFOs) in archive
    members, allowing a malicious backup file uploaded via the recovery
    endpoint to overwrite arbitrary files or create special files on
    disk. Only allow regular files and directories whose resolved path
    stays inside `dest`, and extract each member individually after
    validation.
    """
    safe_members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir()):
            raise BackupRecoverError(
                'Refusing to extract non-regular member in archive: '
                f'{member.name} (type={member.type!r})'
            )
        member_path = path.join(dest, member.name)
        if not _is_within_directory(dest, member_path):
            raise BackupRecoverError(
                f'Refusing to extract unsafe path in archive: {member.name}'
            )
        safe_members.append(member)

    for member in safe_members:
        tar.extract(member, path=dest)


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
