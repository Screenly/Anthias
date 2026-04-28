import os
from pathlib import Path

import click
import pygit2
from python_on_whales import docker

from tools.image_builder.constants import (
    BUILD_TARGET_OPTIONS,
    SERVICES,
    SHORT_HASH_LENGTH,
)
from tools.image_builder.utils import (
    generate_dockerfile,
    get_build_parameters,
    get_docker_tag,
    get_test_context,
    get_uv_builder_context,
    get_viewer_context,
)


def build_image(
    service: str,
    board: str,
    target_platform: str,
    disable_cache_mounts: bool,
    git_hash: str,
    git_short_hash: str,
    git_branch: str,
    environment: str,
    base_image: str,
    docker_tags: list[str],
    clean_build: bool,
    push: bool,
    dockerfiles_only: bool,
) -> None:
    # Enable BuildKit
    os.environ['DOCKER_BUILDKIT'] = '1'

    context = {}

    # Create board-specific cache directory
    cache_dir = Path('/tmp/.buildx-cache') / (
        f'{board}-64'
        if board == 'pi4' and target_platform == 'linux/arm64/v8'
        else board
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.secho(
            f'Warning: Failed to create cache directory: {e}', fg='yellow'
        )

    base_apt_dependencies = [
        'build-essential',
        'cec-utils',
        'curl',
        'ffmpeg',
        'git',
        'git-core',
        'ifupdown',
        'libcec-dev ',
        'libffi-dev',
        'libssl-dev',
        'lsb-release',
        'mplayer',
        'net-tools',
        'procps',
        'psmisc',
        'python3-dev',
        'python3-gi',
        'python3-pil',
        'python3-pip',
        'python3-setuptools',
        'python3-simplejson',
        'python-is-python3',
        'sudo',
        'sqlite3',
    ]

    if board in ['pi1', 'pi2', 'pi3', 'pi4']:
        base_apt_dependencies.extend(['libraspberrypi0'])

    # DEVICE_TYPE inside the container needs to be 'pi4-64' for the 64-bit
    # Pi 4 build, not 'pi4', because lib/github.py:get_latest_docker_hub_hash
    # filters Hub tags by `-{device_type}` suffix and the published tags use
    # `-pi4-64`. Hardware-level checks in viewer/media_player.py go through
    # lib/device_helper.get_device_type() which reads /proc/device-tree/model
    # at runtime and is unaffected.
    device_type = (
        'pi4-64'
        if board == 'pi4' and target_platform == 'linux/arm64/v8'
        else board
    )

    if service == 'viewer':
        context.update(get_viewer_context(board, target_platform))
    elif service == 'test':
        context.update(get_test_context())

    context.update(get_uv_builder_context(service))

    generate_dockerfile(
        service,
        {
            'base_image': base_image,
            'base_image_tag': 'bookworm',
            'base_apt_dependencies': base_apt_dependencies,
            'board': board,
            'device_type': device_type,
            'debian_version': 'bookworm',
            'disable_cache_mounts': disable_cache_mounts,
            'environment': environment,
            'git_branch': git_branch,
            'git_hash': git_hash,
            'git_short_hash': git_short_hash,
            'service': service,
            'target_platform': target_platform,
            **context,
        },
    )

    if service == 'test':
        click.secho(f'Skipping test service for {board}...', fg='yellow')
        return

    if dockerfiles_only:
        return

    # Ensure we're using the correct builder
    try:
        docker.buildx.inspect('multiarch-builder', bootstrap=True)
    except:  # noqa: E722
        docker.buildx.create(name='multiarch-builder', use=True)

    docker.buildx.build(
        context_path='.',
        cache=(not clean_build),
        cache_from={
            'type': 'local',
            'src': str(cache_dir),
        }
        if not clean_build
        else None,
        cache_to={
            'type': 'local',
            'dest': str(cache_dir),
            'mode': 'max',
        }
        if not clean_build
        else None,
        builder='multiarch-builder',
        file=f'docker/Dockerfile.{service}',
        load=True,
        platforms=[target_platform],
        tags=docker_tags,
        push=push,
        progress='plain',
    )


@click.command()
@click.option(
    '--clean-build',
    is_flag=True,
)
@click.option(
    '--build-target',
    default='x86',
    type=click.Choice(BUILD_TARGET_OPTIONS),
)
@click.option(
    '--target-platform',
    help='Override the default target platform',
)
@click.option(
    '--service',
    default=['all'],
    type=click.Choice(
        (
            'all',
            *SERVICES,
        )
    ),
    multiple=True,
)
@click.option(
    '--disable-cache-mounts',
    is_flag=True,
)
@click.option(
    '--environment',
    default='production',
    type=click.Choice(('production', 'development')),
)
@click.option(
    '--push',
    is_flag=True,
)
@click.option(
    '--skip-latest-tag',
    is_flag=True,
    help=(
        'Build/push only the immutable <short-hash>-<board> tag, omitting '
        'the floating latest-<board> / <branch>-<board> tag. Used by CI: '
        'the latest-<board> retag is deferred to a follow-up job that '
        'runs only after every per-platform build in the matrix has '
        'succeeded, so a partial build failure can no longer leave '
        'latest-* pointing at a half-pushed set of images. The retag '
        'step itself is still a sequence of registry calls, not a '
        'single atomic transaction, so a transient registry error '
        'mid-retag can still leave a small subset of latest-* tags '
        'temporarily out of sync until the workflow is re-run.'
    ),
)
@click.option(
    '--dockerfiles-only',
    is_flag=True,
)
def main(
    clean_build: bool,
    build_target: str,
    target_platform: str,
    service: tuple[str, ...],
    disable_cache_mounts: bool,
    environment: str,
    push: bool,
    skip_latest_tag: bool,
    dockerfiles_only: bool,
) -> None:
    git_branch = pygit2.Repository('.').head.shorthand
    git_hash = str(pygit2.Repository('.').head.target)
    git_short_hash = git_hash[:SHORT_HASH_LENGTH]

    build_parameters = get_build_parameters(build_target)
    board = build_parameters['board']
    base_image = build_parameters['base_image']

    # Override target platform if specified
    platform = target_platform or build_parameters['target_platform']
    docker_tag = get_docker_tag(git_branch, board, platform)

    # Determine which services to build
    services_to_build = SERVICES if 'all' in service else list(set(service))

    # Build Docker images
    for service_name in services_to_build:
        # Define tag components.
        #
        # GHCR is listed first because it is the primary, canonical source
        # for Anthias images going forward — `bin/upgrade_containers.sh`
        # regenerates compose from `docker-compose.yml.tmpl`, so flipping
        # the template (separate change) flips every device on next
        # upgrade. Docker Hub stays in the list as a parallel push during
        # the migration window so devices that haven't yet picked up the
        # template flip keep getting `latest-*` advanced.
        #
        # The legacy `screenly/srly-ose-*` namespace was dropped: every
        # device that has run `upgrade_containers.sh` since 2023-02
        # (b9998438) is on `screenly/anthias-*`, and stale `srly-ose-*`
        # `latest-*` mirroring (one of two reasons d568602 hit Docker
        # Hub's 429) gives no real back-compat in exchange.
        namespaces = ['ghcr.io/screenly/anthias', 'screenly/anthias']
        version_suffix = (
            f'{board}-64'
            if board == 'pi4' and platform == 'linux/arm64/v8'
            else f'{board}'
        )

        # Generate all tags
        docker_tags = []
        for namespace in namespaces:
            if not skip_latest_tag:
                # Floating latest-<board> / <branch>-<board> tag.
                docker_tags.append(f'{namespace}-{service_name}:{docker_tag}')
            # Immutable short-hash tag.
            docker_tags.append(
                f'{namespace}-{service_name}:{git_short_hash}-{version_suffix}'
            )

        build_image(
            service_name,
            board,
            platform,
            disable_cache_mounts,
            git_hash,
            git_short_hash,
            git_branch,
            environment,
            base_image,
            docker_tags,
            clean_build,
            push,
            dockerfiles_only,
        )


if __name__ == '__main__':
    main()
