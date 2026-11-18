import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)
SCRIPT = os.path.join(REPO_ROOT, 'bin', 'verify_image_signatures.sh')

SERVER = 'ghcr.io/screenly/anthias-server:latest-pi4-64'
VIEWER = 'ghcr.io/screenly/anthias-viewer:latest-pi4-64'
SERVER_DIGEST = 'ghcr.io/screenly/anthias-server@sha256:' + 'a' * 64
VIEWER_DIGEST = 'ghcr.io/screenly/anthias-viewer@sha256:' + 'b' * 64
WIFI = 'ghcr.io/screenly-labs/wifi-connect:2026.10.13'
WIFI_DIGEST = 'ghcr.io/screenly-labs/wifi-connect@sha256:' + 'd' * 64

IDENTITY = (
    'https://github.com/Screenly/Anthias/.github/workflows/'
    'docker-build.yaml@refs/heads/master'
)
WIFI_IDENTITY = (
    'https://github.com/Screenly-Labs/wifi-connect/.github/workflows/'
    'release.yml@refs/tags/v2026.10.13'
)
ISSUER = 'https://token.actions.githubusercontent.com'

# `docker image inspect --format ... IMAGE` answers with the RepoDigests
# listed for IMAGE in the state file, and fails for an unknown image.
FAKE_DOCKER = """
import json, os, sys
args = sys.argv[1:]
if args[:2] != ['image', 'inspect'] or args[2] != '--format':
    sys.exit(64)
with open(os.environ['FAKE_STATE']) as f:
    digests = json.load(f)['digests']
if args[4] not in digests:
    sys.exit(1)
for ref in digests[args[4]]:
    print(ref)
"""

# `cosign verify` succeeds only for a digest listed as signed, and only
# when asked for the identity that digest was signed under (and the
# GitHub Actions issuer). Every invocation is logged so tests can assert
# what was checked.
FAKE_COSIGN = """
import json, os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_COSIGN_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
with open(os.environ['FAKE_STATE']) as f:
    signed = json.load(f)['signed']
ref = args[-1]
expected = [
    'verify',
    '--certificate-identity', signed.get(ref),
    '--certificate-oidc-issuer', os.environ['EXPECTED_ISSUER'],
]
if ref not in signed or args[:-1] != expected:
    print('Error: no matching signatures', file=sys.stderr)
    sys.exit(1)
print('[{"critical": {}}]')
"""


class Harness:
    def __init__(self, workdir: str) -> None:
        self.workdir = workdir
        self.state_path = os.path.join(workdir, 'state.json')
        self.cosign_log = os.path.join(workdir, 'cosign.log')
        self.bin_dir = os.path.join(workdir, 'bin')
        os.makedirs(self.bin_dir)
        self._write_shim('sudo', '#!/bin/sh\nexec "$@"\n')
        self._write_python_shim('docker', FAKE_DOCKER)
        self._write_python_shim('cosign', FAKE_COSIGN)

    def _write_shim(self, name: str, body: str) -> None:
        path = os.path.join(self.bin_dir, name)
        with open(path, 'w') as f:
            f.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def _write_python_shim(self, name: str, source: str) -> None:
        source_path = os.path.join(self.workdir, f'fake_{name}.py')
        with open(source_path, 'w') as f:
            f.write(source)
        self._write_shim(
            name,
            f'#!/bin/sh\nexec {sys.executable} {source_path} "$@"\n',
        )

    def set_state(
        self,
        digests: dict[str, list[str]],
        signed: dict[str, str],
    ) -> None:
        with open(self.state_path, 'w') as f:
            json.dump({'digests': digests, 'signed': signed}, f)

    def run(
        self,
        *images: str,
        without_cosign: bool = False,
    ) -> 'subprocess.CompletedProcess[str]':
        if without_cosign:
            os.remove(os.path.join(self.bin_dir, 'cosign'))
        env = os.environ.copy()
        # Only the shim directory and the basics the script itself needs,
        # so a cosign installed on the host can't stand in for the fake.
        env['PATH'] = f'{self.bin_dir}:/usr/bin:/bin'
        env['FAKE_STATE'] = self.state_path
        env['FAKE_COSIGN_LOG'] = self.cosign_log
        env['EXPECTED_ISSUER'] = ISSUER
        return subprocess.run(
            [SCRIPT, *images],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @property
    def verified_refs(self) -> list[str]:
        if not os.path.exists(self.cosign_log):
            return []
        with open(self.cosign_log) as f:
            return [json.loads(line)[-1] for line in f if line.strip()]


@pytest.fixture
def harness() -> Iterator[Harness]:
    workdir = tempfile.mkdtemp(prefix='anthias-verify-test-')
    try:
        yield Harness(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_accepts_signed_images(harness: Harness) -> None:
    harness.set_state(
        digests={SERVER: [SERVER_DIGEST], VIEWER: [VIEWER_DIGEST]},
        signed={SERVER_DIGEST: IDENTITY, VIEWER_DIGEST: IDENTITY},
    )

    result = harness.run(SERVER, VIEWER)

    assert result.returncode == 0, result.stderr
    assert harness.verified_refs == [SERVER_DIGEST, VIEWER_DIGEST]
    assert f'Verified signature on {SERVER}' in result.stdout


def test_rejects_an_unsigned_image(harness: Harness) -> None:
    harness.set_state(
        digests={SERVER: [SERVER_DIGEST], VIEWER: [VIEWER_DIGEST]},
        signed={SERVER_DIGEST: IDENTITY},
    )

    result = harness.run(SERVER, VIEWER)

    assert result.returncode == 1
    assert f'Signature verification FAILED for {VIEWER}' in result.stderr
    # cosign's own reason is passed through to the operator.
    assert 'no matching signatures' in result.stderr
    # One bad image doesn't stop the rest being reported on.
    assert harness.verified_refs == [SERVER_DIGEST, VIEWER_DIGEST]


def test_verifies_the_pulled_digest_not_the_tag(harness: Harness) -> None:
    # A digest from another repository on the same image (it was also
    # pulled from the Docker Hub mirror, say) must not be what gets
    # checked against the GHCR reference.
    hub_digest = 'screenly/anthias-server@sha256:' + 'c' * 64
    harness.set_state(
        digests={SERVER: [hub_digest, SERVER_DIGEST]},
        signed={SERVER_DIGEST: IDENTITY},
    )

    result = harness.run(SERVER)

    assert result.returncode == 0, result.stderr
    assert harness.verified_refs == [SERVER_DIGEST]


def test_fails_when_the_image_was_never_pulled(harness: Harness) -> None:
    harness.set_state(digests={}, signed={})

    result = harness.run(SERVER)

    assert result.returncode == 1
    assert f"Can't verify {SERVER}" in result.stderr
    assert harness.verified_refs == []


def test_skips_images_that_are_not_ours(harness: Harness) -> None:
    harness.set_state(
        digests={SERVER: [SERVER_DIGEST]},
        signed={SERVER_DIGEST: IDENTITY},
    )

    result = harness.run(
        SERVER,
        'caddy:2-alpine',
        'ghcr.io/someone-else/anthias-server:latest-pi4-64',
    )

    assert result.returncode == 0, result.stderr
    assert harness.verified_refs == [SERVER_DIGEST]


def test_fails_closed_without_cosign(harness: Harness) -> None:
    harness.set_state(
        digests={SERVER: [SERVER_DIGEST]},
        signed={SERVER_DIGEST: IDENTITY},
    )

    result = harness.run(SERVER, without_cosign=True)

    assert result.returncode == 1
    assert 'cosign is not installed' in result.stderr


def test_requires_at_least_one_image(harness: Harness) -> None:
    harness.set_state(digests={}, signed={})

    result = harness.run()

    assert result.returncode == 2


def test_verifies_wifi_connect_against_its_release(harness: Harness) -> None:
    harness.set_state(
        digests={WIFI: [WIFI_DIGEST]},
        signed={WIFI_DIGEST: WIFI_IDENTITY},
    )

    result = harness.run(WIFI)

    assert result.returncode == 0, result.stderr
    assert harness.verified_refs == [WIFI_DIGEST]


def test_rejects_wifi_connect_signed_for_another_release(
    harness: Harness,
) -> None:
    # A genuinely signed upstream image, re-pushed under the tag Anthias
    # pins, must not pass for the release it was not cut from.
    older = 'ghcr.io/screenly-labs/wifi-connect:2026.10.12'
    harness.set_state(
        digests={older: [WIFI_DIGEST]},
        signed={WIFI_DIGEST: WIFI_IDENTITY},
    )

    result = harness.run(older)

    assert result.returncode == 1
    assert f'Signature verification FAILED for {older}' in result.stderr


def test_rejects_wifi_connect_signed_by_anthias(harness: Harness) -> None:
    harness.set_state(
        digests={WIFI: [WIFI_DIGEST]},
        signed={WIFI_DIGEST: IDENTITY},
    )

    result = harness.run(WIFI)

    assert result.returncode == 1


def test_rejects_wifi_connect_on_a_floating_tag(harness: Harness) -> None:
    latest = 'ghcr.io/screenly-labs/wifi-connect:latest'
    harness.set_state(
        digests={latest: [WIFI_DIGEST]},
        signed={WIFI_DIGEST: WIFI_IDENTITY},
    )

    result = harness.run(latest)

    assert result.returncode == 1
    assert 'not a pinned release' in result.stderr
    assert harness.verified_refs == []
