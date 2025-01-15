from __future__ import unicode_literals

import logging
import sys
import tarfile
from datetime import datetime
from os import getenv, makedirs, path, remove

directories = ['.screenly', 'screenly_assets']
default_archive_name = "anthias-backup"
static_dir = "screenly/staticfiles"


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
    home = getenv('HOME')
    if not home:
        logging.error('No HOME variable')
        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    with tarfile.open(file_path, "r:gz") as tar:
        for directory in directories:
            if directory not in tar.getnames():
                raise Exception("Archive is wrong.")

        tar.extractall(path=getenv('HOME'))

    remove(file_path)
