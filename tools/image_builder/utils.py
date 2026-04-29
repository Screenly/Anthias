import os
from typing import Any

from jinja2 import Environment, FileSystemLoader

from tools.image_builder.constants import GITHUB_REPO_URL


def get_build_parameters(build_target: str) -> dict[str, Any]:
    # Every surviving board now lands on vanilla `debian:trixie`. The
    # `pi2`/`pi3` armhf builds add the Raspberry Pi / Raspbian apt
    # sources at image-build time (see Dockerfile.base.j2); 64-bit and
    # x86 builds need nothing Pi-specific at all.
    if build_target == 'pi5':
        return {
            'board': 'pi5',
            'base_image': 'debian',
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi4-64':
        return {
            'board': 'pi4-64',
            'base_image': 'debian',
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi3':
        return {
            'board': 'pi3',
            'base_image': 'debian',
            'target_platform': 'linux/arm/v7',
        }
    if build_target == 'pi2':
        return {
            'board': 'pi2',
            'base_image': 'debian',
            'target_platform': 'linux/arm/v7',
        }

    return {
        'board': 'x86',
        'base_image': 'debian',
        'target_platform': 'linux/amd64',
    }


def get_docker_tag(git_branch: str, board: str, platform: str) -> str:
    if git_branch == 'master':
        return f'latest-{board}'
    else:
        return f'{git_branch}-{board}'


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


def get_viewer_context(board: str, target_platform: str) -> dict[str, Any]:
    releases_url = f'{GITHUB_REPO_URL}/releases/download'

    # CalVer release of the WebView (YYYY.MM.PATCH). Bump this default
    # together with the corresponding WebView-v* tag in the release.
    # Override via WEBVIEW_VERSION env when building the viewer image
    # ahead of (or against a fork of) the canonical release — useful
    # while the new WebView-v* tag is still being cut, since
    # Dockerfile.viewer.j2 will otherwise 404 when fetching the
    # not-yet-published artifact.
    webview_version = os.environ.get('WEBVIEW_VERSION', '2026.04.0')
    webview_base_url = f'{releases_url}/WebView-v{webview_version}'

    is_qt6 = board in ['pi5', 'pi4-64', 'x86']

    # Qt version is only used to pull the cross-built Qt 5 toolchain
    # archive on legacy 32-bit Pi boards; Qt 6 boards consume Qt from
    # apt and don't need this in the artifact name.
    if is_qt6:
        qt_version = '6.4.2'
    else:
        qt_version = '5.15.14'

    qt_major_version = qt_version.split('.')[0]

    # Viewer-only apt deps. The shared set (build-essential, curl, ffmpeg,
    # git, libcec-dev, libffi-dev, libssl-dev, net-tools, procps,
    # psmisc, python-is-python3, python3-dev, python3-gi, python3-pip,
    # python3-setuptools, sqlite3, sudo, plus libraspberrypi0 on 32-bit
    # Pi boards) is installed by Dockerfile.base.j2 in a layer that
    # server (and test) also use, so it dedups across images. Anything
    # listed here is unique to the viewer image.
    viewer_extra_apt_dependencies = [
        'ca-certificates',
        'dbus-daemon',
        'fonts-arphic-uming',
        'libasound2-dev',
        'libavcodec-dev',
        'libavdevice-dev',
        'libavfilter-dev',
        'libavformat-dev',
        'libavutil-dev',
        'libbz2-dev',
        'libdbus-1-dev',
        'libdbus-glib-1-dev',
        'libdrm-dev',
        'libegl1-mesa-dev',
        'libevent-dev',
        'libfontconfig1-dev',
        'libfreetype6-dev',
        'libgbm-dev',
        'libgcrypt20-dev',
        'libgles2',
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
        'libpng16-16t64',
        'libpq-dev',
        'libpulse-dev',
        'librsvg2-common',
        'libsdl2-dev',
        'libsnappy-dev',
        'libsqlite3-dev',
        'libsrtp2-dev',
        'libswresample-dev',
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
        'python3-netifaces',
        'fonts-wqy-zenhei',
        'vlc',
    ]

    if is_qt6:
        viewer_extra_apt_dependencies.extend(
            [
                'mpv',
                'qt6-base-dev',
                'qt6-webengine-dev',
                'qt6-image-formats-plugins',
            ]
        )
    else:
        # libraspberrypi0 already comes in via base_apt_dependencies on
        # 32-bit Pi boards (see __main__.py), so it's deliberately not
        # repeated here. libssl1.1 is gone in trixie; the rebuilt Qt 5
        # webview archive links against libssl3 from the base image.
        # libgst-dev / libsqlite0-dev / libsrtp0-dev were dropped in
        # trixie — modern equivalents (libgstreamer1.0-dev,
        # libsqlite3-dev, libsrtp2-dev) are pulled by the main viewer
        # apt list above.
        viewer_extra_apt_dependencies.extend(
            [
                'libgstreamer1.0-dev',
                'qt5-image-formats-plugins',
            ]
        )

    return {
        'viewer_extra_apt_dependencies': viewer_extra_apt_dependencies,
        'qt_version': qt_version,
        'qt_major_version': qt_major_version,
        'webview_version': webview_version,
        'webview_base_url': webview_base_url,
        'is_qt6': is_qt6,
        'artifact_board': board,
    }
