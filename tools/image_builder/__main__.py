import click
import pygit2
import requests

from jinja2 import Environment, FileSystemLoader
from pathlib import Path


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


templating_environment = Environment(loader=FileSystemLoader('docker/'))
templating_environment.lstrip_blocks = True
templating_environment.trim_blocks = True


def get_build_parameters(build_target: str) -> dict:
    pi_device_model_path = Path('/proc/device-tree/model')
    default_build_parameters = {
        'board': 'x86',
        'base_image': 'debian',
        'target_platform': 'linux/amd64',
    }

    try:
        with pi_device_model_path.open() as f:
            device_model = f.read().strip()

            if build_target == 'pi4' or 'Raspberry Pi 4' in device_model:
                return {
                    'board': 'pi4',
                    'base_image': 'balenalib/raspberrypi3-debian',
                    'target_platform': 'linux/arm/v8',
                }
            elif build_target == 'pi3' or 'Raspberry Pi 3' in device_model:
                return {
                    'board': 'pi3',
                    'base_image': 'balenalib/raspberrypi3-debian',
                    'target_platform': 'linux/arm/v7',
                }
            elif build_target == 'pi2' or 'Raspberry Pi 2' in device_model:
                return {
                    'board': 'pi2',
                    'base_image': 'balenalib/raspberry-pi2',
                    'target_platform': 'linux/arm/v6',
                }
            elif build_target == 'pi1' or 'Raspberry Pi' in device_model:
                return {
                    'board': 'pi1',
                    'base_image': 'balenalib/raspberry-pi',
                    'target_platform': 'linux/arm/v6',
                }
    except FileNotFoundError:
        return default_build_parameters

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
) -> None:
    context = {}

    if service == 'viewer':
        qt_version = '5.15.2'
        webview_git_hash='4bd295c4a1197a226d537938e947773f4911ca24'
        webview_base_url='https://github.com/Screenly/Anthias/releases/download/WebView-v0.3.1'

        context.update({
            'apt_dependencies': [
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
                'libgst-dev',
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
                'libraspberrypi0',
                'librsvg2-common',
                'libsdl2-dev',
                'libsnappy-dev',
                'libsqlite0-dev',
                'libsqlite3-dev',
                'libsrtp0-dev',
                'libsrtp2-dev',
                'libssl-dev',
                'libzmq3-dev',
                'libssl1.1',
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
            ],
            'qt_version': qt_version,
            'webview_git_hash': webview_git_hash,
            'webview_base_url': webview_base_url,
        })
    elif service == 'test':
        chrome_dl_url="https://storage.googleapis.com/chrome-for-testing-public/123.0.6312.86/linux64/chrome-linux64.zip"
        chromedriver_dl_url="https://storage.googleapis.com/chrome-for-testing-public/123.0.6312.86/linux64/chromedriver-linux64.zip"
    elif service == 'wifi-connect':
        if board == 'x86':
            click.secho('Building wifi-connect for x86 is not supported yet.', fg='red')
            return

        if target_platform == 'linux/arm/v6':
            architecture = 'rpi'
        else:
            architecture = 'armv7hf'

        wc_download_url='https://api.github.com/repos/balena-os/wifi-connect/releases/93025295'

        try:
            response = requests.get(wc_download_url)
            response.raise_for_status()
            data = response.json()
            assets = [
                asset['browser_download_url'] for asset in data['assets']
            ]

            try:
                archive_url = next(
                    asset for asset in assets if f'linux-{architecture}' in asset
                )
            except StopIteration:
                click.secho('No wifi-connect release found for this architecture.', fg='red')
                return

        except requests.exceptions.RequestException as e:
            click.secho(f'Failed to get wifi-connect release: {e}', fg='red')
            return

    # @TODO: Make use of Jinja templates to generate Dockerfiles.
    generate_dockerfile(service, {
        'base_image': 'balenalib/raspberrypi3-debian',
        'base_image_tag': 'bookworm',
        'disable_cache_mounts': disable_cache_mounts,
        **context,
    })


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
def main(
    clean_build: bool,
    build_target: str,
    service,
    disable_cache_mounts: bool,
) -> None:
    git_branch = pygit2.Repository('.').head.shorthand
    git_hash = str(pygit2.Repository('.').head.target)
    git_short_hash = git_hash[:SHORT_HASH_LENGTH]

    build_parameters = get_build_parameters(build_target)
    board = build_parameters['board']
    target_platform = build_parameters['target_platform']

    docker_tag = get_docker_tag(git_branch, board)

    services_to_build = SERVICES if 'all' in service else list(set(service))

    for service in services_to_build:
        build_image(service, board, target_platform, disable_cache_mounts)


if __name__ == "__main__":
    main()
