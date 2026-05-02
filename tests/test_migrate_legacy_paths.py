import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator

import pytest


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)
SCRIPT = os.path.join(REPO_ROOT, 'bin', 'migrate_legacy_paths.sh')


def run_migrate(user_home: str) -> 'subprocess.CompletedProcess[str]':
    env = os.environ.copy()
    env['USER_HOME'] = user_home
    # Slim PATH so the helper resolves only standard binaries. The
    # script's `sudo -n rm` against /etc/sudoers.d/screenly_overrides
    # is best-effort and exits 0 either way (no NOPASSWD on the test
    # host => sudo declines, helper logs and continues), so this run
    # never modifies real /etc state.
    env['PATH'] = '/usr/bin:/bin'
    return subprocess.run(
        [SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def home_dir() -> Iterator[str]:
    home = tempfile.mkdtemp(prefix='anthias-migrate-test-')
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _populate_legacy_layout(home: str) -> None:
    os.makedirs(os.path.join(home, 'screenly', '.git'))
    os.makedirs(os.path.join(home, 'screenly_assets'))
    os.makedirs(os.path.join(home, '.screenly', 'backups'))
    with open(os.path.join(home, '.screenly', 'screenly.db'), 'wb') as f:
        f.write(b'sqlite-stub')
    with open(os.path.join(home, '.screenly', 'screenly.conf'), 'w') as f:
        f.write(
            '[main]\nconfigdir = .screenly\ndatabase = .screenly/screenly.db\n'
        )
    with open(os.path.join(home, 'screenly_assets', 'a.mp4'), 'wb') as f:
        f.write(b'video-stub')


def test_full_migration(home_dir: str) -> None:
    _populate_legacy_layout(home_dir)

    run_migrate(home_dir)

    # New paths exist.
    assert os.path.isdir(os.path.join(home_dir, 'anthias'))
    assert os.path.isdir(os.path.join(home_dir, 'anthias_assets'))
    assert os.path.isdir(os.path.join(home_dir, '.anthias'))

    # Files renamed.
    assert os.path.isfile(os.path.join(home_dir, '.anthias', 'anthias.db'))
    anthias_conf = os.path.join(home_dir, '.anthias', 'anthias.conf')
    assert os.path.isfile(anthias_conf)
    with open(anthias_conf) as f:
        body = f.read()
    assert 'configdir = .anthias' in body
    assert 'database = .anthias/anthias.db' in body
    assert '.screenly' not in body

    # Backups subdir survived the rename.
    assert os.path.isdir(os.path.join(home_dir, '.anthias', 'backups'))

    # Asset content preserved.
    with open(os.path.join(home_dir, 'anthias_assets', 'a.mp4'), 'rb') as f:
        assert f.read() == b'video-stub'

    # Dir-level back-compat symlinks present.
    for legacy, expected_target in (
        ('screenly', os.path.join(home_dir, 'anthias')),
        ('.screenly', os.path.join(home_dir, '.anthias')),
        (
            'screenly_assets',
            os.path.join(home_dir, 'anthias_assets'),
        ),
    ):
        link = os.path.join(home_dir, legacy)
        assert os.path.islink(link), legacy
        assert os.readlink(link) == expected_target

    # File-level back-compat symlinks inside the migrated config dir
    # so a downgrade can still find its expected filenames.
    for legacy_name, expected_target in (
        ('screenly.db', 'anthias.db'),
        ('screenly.conf', 'anthias.conf'),
    ):
        link = os.path.join(home_dir, '.anthias', legacy_name)
        assert os.path.islink(link), legacy_name
        # Relative target so the link is portable across mounts.
        assert os.readlink(link) == expected_target


def test_conf_rewrite_handles_absolute_paths(home_dir: str) -> None:
    os.makedirs(os.path.join(home_dir, '.screenly'))
    # User customised their conf with absolute paths.
    with open(os.path.join(home_dir, '.screenly', 'screenly.conf'), 'w') as f:
        f.write(
            '[main]\n'
            f'configdir = {home_dir}/.screenly\n'
            f'database = {home_dir}/.screenly/screenly.db\n'
        )

    run_migrate(home_dir)

    with open(os.path.join(home_dir, '.anthias', 'anthias.conf')) as f:
        body = f.read()
    assert f'configdir = {home_dir}/.anthias' in body
    assert f'database = {home_dir}/.anthias/anthias.db' in body
    assert '.screenly' not in body


def test_idempotent_rerun(home_dir: str) -> None:
    _populate_legacy_layout(home_dir)
    run_migrate(home_dir)
    # Second run must not raise and must leave the layout intact.
    run_migrate(home_dir)

    assert os.path.isfile(os.path.join(home_dir, '.anthias', 'anthias.db'))
    assert os.path.islink(os.path.join(home_dir, 'screenly'))


def test_fresh_install_noop(home_dir: str) -> None:
    # No legacy paths and no new paths → script should still succeed.
    run_migrate(home_dir)
    assert not os.path.exists(os.path.join(home_dir, 'anthias'))
    assert not os.path.exists(os.path.join(home_dir, 'screenly'))
