import os
import shutil
import subprocess
import tempfile
import unittest


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


class MigrateLegacyPathsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp(prefix='anthias-migrate-test-')

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def _populate_legacy_layout(self) -> None:
        os.makedirs(os.path.join(self.home, 'screenly', '.git'))
        os.makedirs(os.path.join(self.home, 'screenly_assets'))
        os.makedirs(os.path.join(self.home, '.screenly', 'backups'))
        with open(
            os.path.join(self.home, '.screenly', 'screenly.db'), 'wb'
        ) as f:
            f.write(b'sqlite-stub')
        with open(
            os.path.join(self.home, '.screenly', 'screenly.conf'), 'w'
        ) as f:
            f.write(
                '[main]\n'
                'configdir = .screenly\n'
                'database = .screenly/screenly.db\n'
            )
        with open(
            os.path.join(self.home, 'screenly_assets', 'a.mp4'), 'wb'
        ) as f:
            f.write(b'video-stub')

    def test_full_migration(self) -> None:
        self._populate_legacy_layout()

        run_migrate(self.home)

        # New paths exist.
        self.assertTrue(os.path.isdir(os.path.join(self.home, 'anthias')))
        self.assertTrue(
            os.path.isdir(os.path.join(self.home, 'anthias_assets'))
        )
        self.assertTrue(os.path.isdir(os.path.join(self.home, '.anthias')))

        # Files renamed.
        self.assertTrue(
            os.path.isfile(os.path.join(self.home, '.anthias', 'anthias.db'))
        )
        anthias_conf = os.path.join(self.home, '.anthias', 'anthias.conf')
        self.assertTrue(os.path.isfile(anthias_conf))
        with open(anthias_conf) as f:
            body = f.read()
        self.assertIn('configdir = .anthias', body)
        self.assertIn('database = .anthias/anthias.db', body)
        self.assertNotIn('.screenly', body)

        # Backups subdir survived the rename.
        self.assertTrue(
            os.path.isdir(os.path.join(self.home, '.anthias', 'backups'))
        )

        # Asset content preserved.
        with open(
            os.path.join(self.home, 'anthias_assets', 'a.mp4'), 'rb'
        ) as f:
            self.assertEqual(f.read(), b'video-stub')

        # Dir-level back-compat symlinks present.
        for legacy, expected_target in (
            ('screenly', os.path.join(self.home, 'anthias')),
            ('.screenly', os.path.join(self.home, '.anthias')),
            (
                'screenly_assets',
                os.path.join(self.home, 'anthias_assets'),
            ),
        ):
            link = os.path.join(self.home, legacy)
            self.assertTrue(os.path.islink(link), legacy)
            self.assertEqual(os.readlink(link), expected_target)

        # File-level back-compat symlinks inside the migrated config dir
        # so a downgrade can still find its expected filenames.
        for legacy_name, expected_target in (
            ('screenly.db', 'anthias.db'),
            ('screenly.conf', 'anthias.conf'),
        ):
            link = os.path.join(self.home, '.anthias', legacy_name)
            self.assertTrue(os.path.islink(link), legacy_name)
            # Relative target so the link is portable across mounts.
            self.assertEqual(os.readlink(link), expected_target)

    def test_conf_rewrite_handles_absolute_paths(self) -> None:
        os.makedirs(os.path.join(self.home, '.screenly'))
        # User customised their conf with absolute paths.
        with open(
            os.path.join(self.home, '.screenly', 'screenly.conf'), 'w'
        ) as f:
            f.write(
                '[main]\n'
                f'configdir = {self.home}/.screenly\n'
                f'database = {self.home}/.screenly/screenly.db\n'
            )

        run_migrate(self.home)

        with open(os.path.join(self.home, '.anthias', 'anthias.conf')) as f:
            body = f.read()
        self.assertIn(f'configdir = {self.home}/.anthias', body)
        self.assertIn(f'database = {self.home}/.anthias/anthias.db', body)
        self.assertNotIn('.screenly', body)

    def test_idempotent_rerun(self) -> None:
        self._populate_legacy_layout()
        run_migrate(self.home)
        # Second run must not raise and must leave the layout intact.
        run_migrate(self.home)

        self.assertTrue(
            os.path.isfile(os.path.join(self.home, '.anthias', 'anthias.db'))
        )
        self.assertTrue(os.path.islink(os.path.join(self.home, 'screenly')))

    def test_fresh_install_noop(self) -> None:
        # No legacy paths and no new paths → script should still succeed.
        run_migrate(self.home)
        self.assertFalse(os.path.exists(os.path.join(self.home, 'anthias')))
        self.assertFalse(os.path.exists(os.path.join(self.home, 'screenly')))


if __name__ == '__main__':
    unittest.main()
