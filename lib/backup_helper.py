import logging
import sys
import tarfile
from datetime import datetime
from os import getenv, makedirs, path, remove

directories = ['.screenly', 'screenly_assets']
default_archive_name = 'anthias-backup'
static_dir = 'screenly/staticfiles'


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
        for directory in directories:
            if directory not in tar.getnames():
                raise BackupRecoverError('Archive is wrong.')

        _safe_extract(tar, home)

    remove(file_path)
