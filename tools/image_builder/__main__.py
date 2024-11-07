import click
import pygit2
import requests

from jinja2 import Environment, FileSystemLoader
from python_on_whales import docker


SHORT_HASH_LENGTH = 7
BUILD_TARGET_OPTIONS = ['pi1', 'pi2', 'pi3', 'pi4', 'x86']
SERVICES = (
    'server',
    'celery',
    'redis',
    'websocket',
    'nginx',
    'viewer',
    'wifi-connect',
    'test',
)
GITHUB_REPO_URL = 'https://github.com/Screenly/Anthias'


templating_environment = Environment(loader=FileSystemLoader('docker/'))
templating_environment.lstrip_blocks = True
templating_environment.trim_blocks = True


def get_build_parameters(build_target: str) -> dict:
    default_build_parameters = {
        'board': 'x86',
        'base_image': 'debian',
        'target_platform': 'linux/amd64',
    }

    if build_target == 'pi4':
        return {
            'board': 'pi4',
            'base_image': 'balenalib/raspberrypi3-debian',
            'target_platform': 'linux/arm/v8',
        }
    elif build_target == 'pi3':
        return {
            'board': 'pi3',
            'base_image': 'balenalib/raspberrypi3-debian',
            'target_platform': 'linux/arm/v7',
        }
    elif build_target == 'pi2':
        return {
            'board': 'pi2',
            'base_image': 'balenalib/raspberry-pi2',
            'target_platform': 'linux/arm/v6',
        }
    elif build_target == 'pi1':
        return {
            'board': 'pi1',
            'base_image': 'balenalib/raspberry-pi',
            'target_platform': 'linux/arm/v6',
        }

    return default_build_parameters


def get_docker_tag(git_branch: str, board: str) -> str:
    if git_branch == 'master':
        return f'latest-{board}'
    else:
        return f'{git_branch}-{board}'


def generate_dockerfile(service: str, context: dict) -> None:
    template = templating_environment.get_template(f'Dockerfile.{service}.j2')
    dockerfile = template.render(**context)

    with open(f'docker/Dockerfile.{service}', 'w') as f:
        f.write(dockerfile)


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
    context = {}

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
        webview_git_hash = '5e556681738a1fa918dc9f0bf5879ace2e603e12'
        releases_url = f'{GITHUB_REPO_URL}/releases/download'
        webview_base_url = f'{releases_url}/WebView-v0.3.3'

        qt_version = '6.6.3' if board == 'x86' else '5.15.14'
        qt_major_version = qt_version.split('.')[0]

        apt_dependencies = [
            'build-essential',
            'ca-certificates',
            'curl',
            'dbus-daemon',
            'fonts-arphic-uming',
            'git-core',
            'libasound2-dev',
            'libavcodec-dev',
            'libavformat-dev',
            'libavutil-dev',
            'libbz2-dev',
            'libcec-dev ',
            'libdbus-1-dev',
            'libdbus-glib-1-dev',
            'libdrm-dev',
            'libegl1-mesa-dev',
            'libevent-dev',
            'libffi-dev',
            'libfontconfig1-dev',
            'libfreetype6-dev',
            'libgbm-dev',
            'libgcrypt20-dev',
            'libgles2-mesa',
            'libgles2-mesa-dev',
            'libglib2.0-dev',
            'libicu-dev',
            'libinput-dev',
            'libiodbc2-dev',
            'libjpeg62-turbo-dev',
            'libjsoncpp-dev',
            'libminizip-dev',
            'libnss3',
            'libnss3-dev',
            'libnss3-tools',
            'libopus-dev',
            'libpci-dev',
            'libpng-dev',
            'libpng16-16',
            'libpq-dev',
            'libpulse-dev',
            'librsvg2-common',
            'libsdl2-dev',
            'libsnappy-dev',
            'libsqlite3-dev',
            'libsrtp2-dev',
            'libssl-dev',
            'libzmq3-dev',
            'libswscale-dev',
            'libsystemd-dev',
            'libts-dev',
            'libudev-dev',
            'libvpx-dev',
            'libwebp-dev',
            'libx11-dev',
            'libx11-xcb-dev',
            'libx11-xcb1',
            'libxcb-glx0-dev',
            'libxcb-icccm4',
            'libxcb-icccm4-dev',
            'libxcb-image0',
            'libxcb-image0-dev',
            'libxcb-keysyms1',
            'libxcb-keysyms1-dev',
            'libxcb-randr0-dev',
            'libxcb-render-util0',
            'libxcb-render-util0-dev',
            'libxcb-shape0-dev',
            'libxcb-shm0',
            'libxcb-shm0-dev',
            'libxcb-sync-dev',
            'libxcb-sync1',
            'libxcb-xfixes0-dev',
            'libxcb-xinerama0',
            'libxcb-xinerama0-dev',
            'libxcb1',
            'libxcb1-dev',
            'libxext-dev',
            'libxi-dev',
            'libxkbcommon-dev',
            'libxrender-dev',
            'libxslt1-dev',
            'libxss-dev',
            'libxtst-dev',
            'libzmq5-dev',
            'libzmq5',
            'net-tools',
            'procps',
            'psmisc',
            'python3-dev',
            'python3-gi',
            'python3-netifaces',
            'python3-pip',
            'python3-setuptools',
            'python-is-python3',
            'ttf-wqy-zenhei',
            'vlc',
            'sudo',
            'sqlite3',
            'ffmpeg',
            'libavcodec-dev',
            'libavdevice-dev',
            'libavfilter-dev',
            'libavformat-dev',
            'libavutil-dev',
            'libswresample-dev',
            'libswscale-dev',
        ]

        if board != 'x86':
            apt_dependencies.extend([
                'libraspberrypi0',
                'libgst-dev',
                'libsqlite0-dev',
                'libsrtp0-dev',
            ])

            if board != 'pi1':
                apt_dependencies.extend(['libssl1.1'])

        context.update({
            'apt_dependencies': apt_dependencies,
            'qt_version': qt_version,
            'qt_major_version': qt_major_version,
            'webview_git_hash': webview_git_hash,
            'webview_base_url': webview_base_url,
        })

    elif service == 'test':
        chrome_dl_url = (
            'https://storage.googleapis.com/chrome-for-testing-public/'
            '123.0.6312.86/linux64/chrome-linux64.zip'
        )
        chromedriver_dl_url = (
            'https://storage.googleapis.com/chrome-for-testing-public/'
            '123.0.6312.86/linux64/chromedriver-linux64.zip'
        )

        context.update({
            'apt_dependencies': [
                'wget',
                'unzip',
                'libnss3',
                'libatk1.0-0',
                'libatk-bridge2.0.0',
                'libcups2',
                'libxcomposite1',
                'libxdamage1',
            ],
            'chrome_dl_url': chrome_dl_url,
            'chromedriver_dl_url': chromedriver_dl_url,
        })
    elif service == 'wifi-connect':
        if target_platform == 'linux/arm/v6':
            architecture = 'rpi'
        elif target_platform in ['linux/arm/v7', 'linux/arm/v8']:
            architecture = 'armv7hf'
        elif target_platform == 'linux/amd64':
            architecture = 'amd64'

        wc_download_url = (
            'https://api.github.com/repos/balena-os/wifi-connect/'
            'releases/93025295'
        )

        try:
            response = requests.get(wc_download_url)
            response.raise_for_status()
            data = response.json()
            assets = [
                asset['browser_download_url'] for asset in data['assets']
            ]

            try:
                archive_url = next(
                    asset for asset in assets
                    if f'linux-{architecture}' in asset
                )
            except StopIteration:
                click.secho(
                    'No wifi-connect release found for this architecture.',
                    fg='red',
                )
                archive_url = ""

        except requests.exceptions.RequestException as e:
            click.secho(f'Failed to get wifi-connect release: {e}', fg='red')
            return

        context.update({
            'apt_dependencies': [
                'dnsmasq',
                'iw',
                'network-manager',
                'unzip',
                'wget',
                'wireless-tools',
            ],
            'archive_url': archive_url,
        })
    elif service == 'nginx':
        context.update({
            'environment': environment,
        })

    generate_dockerfile(service, {
        'base_image': base_image,
        'base_image_tag': 'bookworm',
        'base_apt_dependencies': base_apt_dependencies,
        'board': board,
        'debian_version': 'bookworm',
        'disable_cache_mounts': disable_cache_mounts,
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

    docker.buildx.build(
        context_path='.',
        cache=(not clean_build),
        cache_from={
            'type': 'local',
            'src': '/tmp/.buildx-cache',
        },
        cache_to={
            'type': 'local',
            'dest': '/tmp/.buildx-cache',
        },
        file=f'docker/Dockerfile.{service}',
        load=True,
        platforms=[target_platform],
        tags=docker_tags,
    )

    if push:
        docker.push(docker_tags)


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
def main(
    clean_build: bool,
    build_target: str,
    service,
    disable_cache_mounts: bool,
    environment: str,
    push: bool,
    dockerfiles_only: bool,
) -> None:
    git_branch = pygit2.Repository('.').head.shorthand
    git_hash = str(pygit2.Repository('.').head.target)
    git_short_hash = git_hash[:SHORT_HASH_LENGTH]

    build_parameters = get_build_parameters(build_target)
    board = build_parameters['board']
    target_platform = build_parameters['target_platform']
    base_image = build_parameters['base_image']

    docker_tag = get_docker_tag(git_branch, board)
    services_to_build = SERVICES if 'all' in service else list(set(service))

    for service in services_to_build:
        docker_tags = [
            f'screenly/anthias-{service}:{docker_tag}',
            f'screenly/anthias-{service}:{git_short_hash}-{board}',
            f'screenly/srly-ose-{service}:{docker_tag}',
            f'screenly/srly-ose-{service}:{git_short_hash}-{board}',
        ]

        build_image(
            service,
            board,
            target_platform,
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


if __name__ == "__main__":
    main()
