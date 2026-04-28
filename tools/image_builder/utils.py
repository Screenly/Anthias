from typing import Any

from jinja2 import Environment, FileSystemLoader

from tools.image_builder.constants import GITHUB_REPO_URL


def get_build_parameters(build_target: str) -> dict[str, Any]:
    default_build_parameters = {
        'board': 'x86',
        'base_image': 'debian',
        'target_platform': 'linux/amd64',
    }

    if build_target == 'pi5':
        return {
            'board': 'pi5',
            'base_image': 'balenalib/raspberrypi5-debian',
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi4':
        return {
            'board': 'pi4',
            'base_image': 'balenalib/raspberrypi3-debian',
            'target_platform': 'linux/arm/v8',
        }
    elif build_target == 'pi4-64':
        return {
            'board': 'pi4',
            'base_image': 'balenalib/raspberrypi3-debian',
            'target_platform': 'linux/arm64/v8',
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


def get_docker_tag(git_branch: str, board: str, platform: str) -> str:
    result_board = board

    if platform == 'linux/arm64/v8' and board == 'pi4':
        result_board = f'{board}-64'

    if git_branch == 'master':
        return f'latest-{result_board}'
    else:
        return f'{git_branch}-{result_board}'


def generate_dockerfile(service: str, context: dict[str, Any]) -> None:
    templating_environment = Environment(loader=FileSystemLoader('docker/'))
    templating_environment.lstrip_blocks = True
    templating_environment.trim_blocks = True

    template = templating_environment.get_template(f'Dockerfile.{service}.j2')
    dockerfile = template.render(**context)

    with open(f'docker/Dockerfile.{service}', 'w') as f:
        f.write(dockerfile)


def get_uv_builder_context(service: str) -> dict[str, Any]:
    service_to_group = {
        'server': 'server',
        'celery': 'server',
        'viewer': 'viewer',
        'test': 'test',
    }

    uv_group = service_to_group.get(service)
    if uv_group is None:
        return {}

    groups_needing_native_build_libs = {'server', 'viewer', 'test'}
    builder_extra_apt = []
    if uv_group in groups_needing_native_build_libs:
        builder_extra_apt = [
            'libcec-dev',
            'libdbus-1-dev',
            'libdbus-glib-1-dev',
        ]

    return {
        'uv_group': uv_group,
        'builder_extra_apt': builder_extra_apt,
        'uv_system_site_packages': service in {'viewer', 'test'},
    }


def get_test_context() -> dict[str, Any]:
    chrome_dl_url = (
        'https://storage.googleapis.com/chrome-for-testing-public/'
        '123.0.6312.86/linux64/chrome-linux64.zip'
    )
    chromedriver_dl_url = (
        'https://storage.googleapis.com/chrome-for-testing-public/'
        '123.0.6312.86/linux64/chromedriver-linux64.zip'
    )

    return {
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
    }


def get_viewer_context(board: str) -> dict[str, Any]:
    releases_url = f'{GITHUB_REPO_URL}/releases/download'

    webview_git_hash = 'd7a7e2c'
    webview_base_url = f'{releases_url}/WebView-v0.3.12'

    qt_version = '5.15.14'

    if board in ['pi5', 'x86']:
        qt_version = '6.4.2'
    else:
        qt_version = '5.15.14'

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

    if board in ['pi5', 'x86']:
        apt_dependencies.extend(
            [
                'mpv',
                'qt6-base-dev',
                'qt6-webengine-dev',
                'qt6-image-formats-plugins',
            ]
        )

    if board not in ['x86', 'pi5']:
        apt_dependencies.extend(
            [
                'libraspberrypi0',
                'libgst-dev',
                'libsqlite0-dev',
                'libsrtp0-dev',
                'qt5-image-formats-plugins',
            ]
        )

        if board != 'pi1':
            apt_dependencies.extend(['libssl1.1'])

    return {
        'apt_dependencies': apt_dependencies,
        'qt_version': qt_version,
        'qt_major_version': qt_major_version,
        'webview_git_hash': webview_git_hash,
        'webview_base_url': webview_base_url,
    }
