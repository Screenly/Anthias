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
    get_viewer_context,
    get_wifi_connect_context,
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
    disable_registry_cache: bool,
) -> None:
    # Enable BuildKit
    os.environ['DOCKER_BUILDKIT'] = '1'
    os.environ['BUILDKIT_INLINE_CACHE'] = '1'

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
            f'Warning: Failed to create cache directory: {e}',
            fg='yellow'
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
        'libzmq3-dev',
        'libzmq5-dev',
        'libzmq5',
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

    if service == 'viewer':
        context.update(get_viewer_context(board))
    elif service == 'test':
        context.update(get_test_context())
    elif service == 'wifi-connect':
        context.update(get_wifi_connect_context(target_platform))
    elif service == 'server':
        if environment == 'development':
            base_apt_dependencies.extend(['nodejs', 'npm'])

    generate_dockerfile(service, {
        'base_image': base_image,
        'base_image_tag': 'bookworm',
        'base_apt_dependencies': base_apt_dependencies,
        'board': board,
        'debian_version': 'bookworm',
        'disable_cache_mounts': disable_cache_mounts,
        'environment': environment,
        'git_branch': git_branch,
        'git_hash': git_hash,
        'git_short_hash': git_short_hash,
        **context,
    })

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
        cache_from=[
            {
                'type': 'local',
                'src': str(cache_dir),
            },
            {
                'type': 'registry',
                'ref': f'screenly/anthias-{service}:latest-pi4-64',
            } if board == 'pi4' and target_platform == 'linux/arm64/v8' else {
                'type': 'registry',
                'ref': f'screenly/anthias-{service}:latest-{board}',
            }
        ] if not clean_build and not disable_registry_cache else None,
        cache_to={
            'type': 'local',
            'dest': str(cache_dir),
            'mode': 'max',
        } if not clean_build else None,
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
    type=click.Choice((
        'all',
        *SERVICES,
    )),
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
    '--dockerfiles-only',
    is_flag=True,
)
@click.option(
    '--disable-registry-cache',
    is_flag=True,
    help='Disable caching from Docker Hub images',
)
def main(
    clean_build: bool,
    build_target: str,
    target_platform: str,
    service,
    disable_cache_mounts: bool,
    environment: str,
    push: bool,
    dockerfiles_only: bool,
    disable_registry_cache: bool,
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
        # Define tag components
        namespaces = ['screenly/anthias', 'screenly/srly-ose']
        version_suffix = (
            f'{board}-64' if board == 'pi4' and platform == 'linux/arm64/v8'
            else f'{board}'
        )

        # Generate all tags
        docker_tags = []
        for namespace in namespaces:
            # Add latest/branch tags
            docker_tags.append(f'{namespace}-{service_name}:{docker_tag}')
            # Add version tags
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
            disable_registry_cache,
        )


if __name__ == "__main__":
    main()
