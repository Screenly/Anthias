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
) -> None:
    if service == 'viewer':
        qt_version = '5.15.2'
        webview_git_hash='4bd295c4a1197a226d537938e947773f4911ca24'
        webview_base_url='https://github.com/Screenly/Anthias/releases/download/WebView-v0.3.1'
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
def main(
    clean_build: bool,
    build_target: str,
    service,
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
        build_image(service, board, target_platform)


if __name__ == "__main__":
    main()
