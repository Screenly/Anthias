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
SCRIPT = os.path.join(REPO_ROOT, 'bin', 'purge_stale_images.sh')
FAKE_DOCKER = os.path.join(REPO_ROOT, 'tests', 'fake_docker.py')

SERVER_REPO = 'ghcr.io/screenly/anthias-server'
VIEWER_REPO = 'ghcr.io/screenly/anthias-viewer'

ImageRow = dict[str, object]


class Harness:
    def __init__(self, workdir: str) -> None:
        self.workdir = workdir
        self.state_path = os.path.join(workdir, 'state.json')
        self.removed_log = os.path.join(workdir, 'removed.log')
        self.bin_dir = os.path.join(workdir, 'bin')
        os.makedirs(self.bin_dir)
        self._write_shim('sudo', '#!/bin/sh\nexec "$@"\n')
        self._write_shim(
            'docker',
            f'#!/bin/sh\nexec {sys.executable} {FAKE_DOCKER} "$@"\n',
        )

    def _write_shim(self, name: str, body: str) -> None:
        path = os.path.join(self.bin_dir, name)
        with open(path, 'w') as f:
            f.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def set_state(
        self,
        images: list[ImageRow],
        containers: dict[str, str],
    ) -> None:
        with open(self.state_path, 'w') as f:
            json.dump({'images': images, 'containers': containers}, f)

    def run(
        self,
        docker_fails: bool = False,
    ) -> 'subprocess.CompletedProcess[str]':
        env = os.environ.copy()
        # The shims go first so `docker` and `sudo` resolve to them
        # rather than to anything the host happens to have installed.
        env['PATH'] = f'{self.bin_dir}:{env.get("PATH", "/usr/bin:/bin")}'
        env['FAKE_DOCKER_STATE'] = self.state_path
        env['FAKE_DOCKER_REMOVED_LOG'] = self.removed_log
        if docker_fails:
            env['FAKE_DOCKER_FAIL'] = '1'
        return subprocess.run(
            [SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    @property
    def removed(self) -> list[str]:
        if not os.path.exists(self.removed_log):
            return []
        with open(self.removed_log) as f:
            return [line.strip() for line in f if line.strip()]

    @property
    def remaining_ids(self) -> set[str]:
        with open(self.state_path) as f:
            return {image['id'] for image in json.load(f)['images']}


@pytest.fixture
def harness() -> Iterator[Harness]:
    workdir = tempfile.mkdtemp(prefix='anthias-purge-test-')
    try:
        yield Harness(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def image(
    image_id: str,
    repo: str | None = None,
    tag: str | None = None,
    size: int = 1024,
) -> ImageRow:
    """
    One row of `docker images`. Rows sharing an ID are the same image
    under several references, which is how a tagged and an untagged view
    of the same layers coexist.
    """

    return {'id': image_id, 'repo': repo, 'tag': tag, 'size': size}


def test_removes_superseded_tags_keeps_running_stack(
    harness: Harness,
) -> None:
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'v0.20.0-pi4-64'),
            image('sha256:old', SERVER_REPO, 'v0.19.5-pi4-64'),
            image('sha256:older', SERVER_REPO, 'v0.19.4-pi4-64'),
        ],
        containers={'container-server': 'sha256:new'},
    )

    result = harness.run()

    assert sorted(harness.removed) == [
        f'{SERVER_REPO}:v0.19.4-pi4-64',
        f'{SERVER_REPO}:v0.19.5-pi4-64',
    ]
    assert harness.remaining_ids == {'sha256:new'}
    assert (
        f'Reclaimed superseded image {SERVER_REPO}:v0.19.5-pi4-64'
        in result.stdout
    )


def test_keeps_images_of_stopped_containers(harness: Harness) -> None:
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'v0.20.0-pi4-64'),
            image('sha256:stopped', VIEWER_REPO, 'v0.19.5-pi4-64'),
        ],
        containers={
            'container-server': 'sha256:new',
            'container-viewer-old': 'sha256:stopped',
        },
    )

    result = harness.run()

    assert harness.removed == []
    assert harness.remaining_ids == {'sha256:new', 'sha256:stopped'}
    assert result.stdout.strip() == ''


def test_leaves_third_party_images_alone(harness: Harness) -> None:
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'v0.20.0-pi4-64'),
            image('sha256:old', SERVER_REPO, 'v0.19.5-pi4-64'),
            image('sha256:grafana', 'grafana/grafana', 'latest'),
            image('sha256:nodered', 'nodered/node-red', '3.1'),
        ],
        containers={'container-server': 'sha256:new'},
    )

    harness.run()

    assert harness.removed == [f'{SERVER_REPO}:v0.19.5-pi4-64']
    assert harness.remaining_ids == {
        'sha256:new',
        'sha256:grafana',
        'sha256:nodered',
    }


def test_removes_legacy_docker_hub_images(harness: Harness) -> None:
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'v0.20.0-pi4-64'),
            image('sha256:hub', 'screenly/anthias-viewer', 'latest-pi4'),
            image('sha256:ose', 'screenly/srly-ose-server', 'latest-pi4'),
        ],
        containers={'container-server': 'sha256:new'},
    )

    harness.run()

    assert sorted(harness.removed) == [
        'screenly/anthias-viewer:latest-pi4',
        'screenly/srly-ose-server:latest-pi4',
    ]
    assert harness.remaining_ids == {'sha256:new'}


def test_removes_digest_only_rows_by_id(harness: Harness) -> None:
    # A device tracking `latest` re-pulls the same tag every upgrade. On
    # the classic image store the outgoing image keeps its digest
    # reference and loses only its tag, so it shows up under the Anthias
    # repository with no tag to remove it by.
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'latest-pi4-64'),
            image('sha256:previous', SERVER_REPO, None),
        ],
        containers={'container-server': 'sha256:new'},
    )

    harness.run()

    assert harness.removed == ['sha256:previous']
    assert harness.remaining_ids == {'sha256:new'}


def test_keeps_digest_only_row_shared_with_another_repository(
    harness: Harness,
) -> None:
    # Removing by ID has to stay a no-op when the ID also answers to a
    # tag outside the Anthias repositories, so an operator's own tag
    # survives.
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'latest-pi4-64'),
            image('sha256:shared', SERVER_REPO, None),
            image('sha256:shared', 'localhost/my-build', 'v1'),
        ],
        containers={'container-server': 'sha256:new'},
    )

    result = harness.run()

    assert harness.removed == []
    assert harness.remaining_ids == {'sha256:new', 'sha256:shared'}
    # Nothing to announce: the daemon turned the removal down.
    assert result.stdout.strip() == ''


def test_sweeps_untagged_images(harness: Harness) -> None:
    # Under the containerd image store the outgoing image loses every
    # reference it had, so nothing identifies it as ours. Those are
    # unreachable by name for anyone, and `docker image prune` is what
    # collects them.
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'latest-pi4-64'),
            image('sha256:untagged', size=4096),
            image('sha256:untagged-in-use'),
        ],
        containers={
            'container-server': 'sha256:new',
            'container-other': 'sha256:untagged-in-use',
        },
    )

    result = harness.run()

    assert harness.removed == ['sha256:untagged']
    assert 'Reclaimed 4096B of untagged images.' in result.stdout
    assert harness.remaining_ids == {'sha256:new', 'sha256:untagged-in-use'}


def test_second_run_is_a_noop(harness: Harness) -> None:
    harness.set_state(
        images=[
            image('sha256:new', SERVER_REPO, 'v0.20.0-pi4-64'),
            image('sha256:old', SERVER_REPO, 'v0.19.5-pi4-64'),
        ],
        containers={'container-server': 'sha256:new'},
    )

    harness.run()
    second = harness.run()

    assert harness.removed == [f'{SERVER_REPO}:v0.19.5-pi4-64']
    assert second.stdout.strip() == ''


def test_unreachable_docker_is_not_an_error(harness: Harness) -> None:
    harness.set_state(
        images=[image('sha256:old', SERVER_REPO, 'v0.19.5-pi4-64')],
        containers={},
    )

    result = harness.run(docker_fails=True)

    assert result.returncode == 0
    assert 'Skipping stale image cleanup' in result.stderr
    assert harness.removed == []
    assert harness.remaining_ids == {'sha256:old'}


def test_no_containers_at_all_still_purges(harness: Harness) -> None:
    # `docker compose down` before an interrupted upgrade can leave the
    # host with no containers; the images are still ours to reclaim.
    harness.set_state(
        images=[image('sha256:old', SERVER_REPO, 'v0.19.5-pi4-64')],
        containers={},
    )

    harness.run()

    assert harness.removed == [f'{SERVER_REPO}:v0.19.5-pi4-64']
    assert harness.remaining_ids == set()
