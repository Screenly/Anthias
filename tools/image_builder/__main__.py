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
    cache_backend: str,
) -> None:
    # Enable BuildKit
    os.environ['DOCKER_BUILDKIT'] = '1'

    context = {}

    # Local cache: per-board on-disk directory under the user's
    # XDG-style cache home (override via $XDG_CACHE_HOME). Per-user
    # rather than under /tmp so a multi-user host doesn't share
    # buildkit cache state across accounts. Unused by the registry
    # backend, which pushes to GHCR instead.
    cache_scope = board
    xdg_cache_home = (
        Path(os.environ['XDG_CACHE_HOME'])
        if os.environ.get('XDG_CACHE_HOME')
        else Path.home() / '.cache'
    )
    cache_dir = xdg_cache_home / 'anthias-buildx' / cache_scope
    if cache_backend == 'local':
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            click.secho(
                f'Warning: Failed to create cache directory: {e}',
                fg='yellow',
            )

    base_apt_dependencies = [
        'build-essential',
        'cec-utils',
        'curl',
        'ffmpeg',
        'git',
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

    # The 32-bit Pi boards (pi2, pi3) link against Broadcom's legacy
    # userland (libbcm_host, libmmal, libvchiq_arm) at runtime via
    # libraspberrypi0. Pull it from archive.raspbian.org's `rpi`
    # component — Trixie's archive.raspberrypi.org/main no longer
    # ships it (replaced by raspi-utils, which doesn't cover the
    # Qt 5 webview link path). 64-bit boards don't need it: their
    # Qt 6 viewer uses KMS/DRM directly.
    is_legacy_pi_armhf = board in ['pi2', 'pi3']
    if is_legacy_pi_armhf:
        base_apt_dependencies.extend(['libraspberrypi0'])

    if service == 'viewer':
        context.update(get_viewer_context(board, target_platform))
    elif service == 'test':
        context.update(get_test_context())

    context.update(get_uv_builder_context(service))

    generate_dockerfile(
        service,
        {
            'base_image': base_image,
            'base_image_tag': 'trixie',
            'base_apt_dependencies': base_apt_dependencies,
            'board': board,
            'device_type': board,
            'debian_version': 'trixie',
            'disable_cache_mounts': disable_cache_mounts,
            'environment': environment,
            'git_branch': git_branch,
            'git_hash': git_hash,
            'git_short_hash': git_short_hash,
            'is_legacy_pi_armhf': is_legacy_pi_armhf,
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

    # Resolve cache_from / cache_to. `--clean-build` short-circuits both
    # to None for a true cold rebuild. Otherwise we pick a backend:
    #
    #   * local    — board-scoped on-disk directory at
    #     $XDG_CACHE_HOME/anthias-buildx/<board> (typically
    #     ~/.cache/anthias-buildx/<board>). Used for local dev so
    #     cache state survives across `tools.image_builder`
    #     invocations on the same machine.
    #   * registry — BuildKit's registry cache backend
    #     (https://docs.docker.com/build/cache/backends/registry/).
    #     Pushes cache to a tagged image at
    #     <namespace>-<service>:buildcache-<board>. Reuses the GHCR
    #     login already done by CI — no extra tokens or third-party
    #     actions needed — and inherits GHCR's free unlimited
    #     storage for public packages. Cache lives next to the real
    #     image tags but with a `buildcache-*` prefix so it can't
    #     collide with the immutable <short-hash>-<board> or
    #     floating latest-<board> tags.
    if clean_build:
        cache_from = None
        cache_to = None
    elif cache_backend == 'registry':
        # Hardcode the GHCR-primary namespace so the cache lives next to
        # the published images for this service. Doesn't read from
        # `namespaces` below: cache only needs one canonical home, and
        # GHCR's free unlimited storage for public packages makes it the
        # right one. If the namespaces list changes in the future, this
        # ref needs to move with it.
        cache_ref = (
            f'ghcr.io/screenly/anthias-{service}:buildcache-{cache_scope}'
        )
        # Reads are always safe — anthias-* GHCR packages are public,
        # so cache_from works without auth (matters for someone
        # invoking this locally with --cache-backend=registry to
        # warm-start off CI's cache).
        cache_from = {'type': 'registry', 'ref': cache_ref}
        if push:
            cache_to = {
                'type': 'registry',
                'ref': cache_ref,
                'mode': 'max',
                # `image-manifest=true` writes the cache as an OCI
                # image manifest rather than the legacy index-only
                # form, which is the only thing GHCR will accept
                # under the ghcr.io/screenly/anthias-* repos (it
                # rejects standalone cache manifests). Cheap, just
                # affects how the cache blob is wrapped.
                'image-manifest': 'true',
            }
        else:
            # Without --push the build hasn't authenticated to GHCR,
            # so trying to write cache there would fail mid-build.
            # Read-only: pull layers from the published cache, don't
            # update it.
            cache_to = None
            click.secho(
                f'cache-backend=registry without --push: reading from '
                f'{cache_ref} but not writing back.',
                fg='yellow',
            )
    else:
        cache_from = {'type': 'local', 'src': str(cache_dir)}
        cache_to = {
            'type': 'local',
            'dest': str(cache_dir),
            'mode': 'max',
        }

    docker.buildx.build(
        context_path='.',
        cache=(not clean_build),
        cache_from=cache_from,
        cache_to=cache_to,
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
@click.option(
    '--cache-backend',
    type=click.Choice(['local', 'registry']),
    default='local',
    envvar='BUILDX_CACHE_BACKEND',
    help=(
        'BuildKit cache backend. `local` (default) writes to '
        '$XDG_CACHE_HOME/anthias-buildx/<board>/ (typically '
        '~/.cache/anthias-buildx/) and is right for local dev. '
        '`registry` pushes the cache to '
        'ghcr.io/screenly/anthias-<service>:buildcache-<board> for '
        'CI — reuses the GHCR login already done by the workflow, '
        'no extra tokens needed. Override via $BUILDX_CACHE_BACKEND.'
    ),
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
    cache_backend: str,
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

        # Generate all tags
        docker_tags = []
        for namespace in namespaces:
            if not skip_latest_tag:
                # Floating latest-<board> / <branch>-<board> tag.
                docker_tags.append(f'{namespace}-{service_name}:{docker_tag}')
            # Immutable short-hash tag.
            docker_tags.append(
                f'{namespace}-{service_name}:{git_short_hash}-{board}'
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
            cache_backend,
        )


if __name__ == '__main__':
    main()
