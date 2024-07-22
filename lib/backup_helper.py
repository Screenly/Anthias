from __future__ import unicode_literals
import logging
import tarfile
import sys
from datetime import datetime
from os import path, getenv, makedirs, remove

directories = ['.screenly', 'screenly_assets']
default_archive_name = "anthias-backup"
static_dir = "screenly/static"


def create_backup(name=default_archive_name):
    home = getenv('HOME')
    archive_name = "{}-{}.tar.gz".format(
        name if name else default_archive_name,
        datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    )
    file_path = path.join(home, static_dir, archive_name)

    if not path.exists(path.join(home, static_dir)):
        makedirs(path.join(home, static_dir), exist_ok=True)

    if path.isfile(file_path):
        remove(file_path)

    try:
        with tarfile.open(file_path, "w:gz") as tar:

            for directory in directories:
                path_to_dir = path.join(home, directory)
                tar.add(path_to_dir, arcname=directory)
    except IOError as e:
        remove(file_path)
        raise e

    return archive_name


def recover(file_path):
    HOME = getenv('HOME')
    if not HOME:
        logging.error('No HOME variable')
        sys.exit(1)  # Alternatively, we can raise an Exception using a custom message, or we can create a new class that extends Exception.

    with tarfile.open(file_path, "r:gz") as tar:
        for directory in directories:
            if directory not in tar.getnames():
                raise Exception("Archive is wrong.")

        tar.extractall(path=getenv('HOME'))

    remove(file_path)
