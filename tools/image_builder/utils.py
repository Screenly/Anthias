from typing import Any

from jinja2 import Environment, FileSystemLoader

from tools.image_builder.constants import BASE_IMAGE, GITHUB_REPO_URL


def get_build_parameters(build_target: str) -> dict[str, Any]:
    # Every surviving board now lands on vanilla `debian:trixie`. The
    # `pi2`/`pi3` armhf builds add the Raspberry Pi / Raspbian apt
    # sources at image-build time (see Dockerfile.base.j2); 64-bit and
    # x86 builds need nothing Pi-specific at all.
    if build_target == 'pi5':
        return {
            'board': 'pi5',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi4-64':
        return {
            'board': 'pi4-64',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'arm64':
        # Generic 64-bit ARM SBC fallback (Orange Pi, Rock Pi, Banana Pi, …).
        # Effectively a thinner pi5 variant: Qt 6, arm64, no libraspberrypi0
        # / Broadcom userland in the runtime base. The viewer wiring mirrors
        # x86's cage + wayland path because non-Pi ARM SBCs typically have
        # no /dev/fb0 (Armbian boots Mesa straight to DRM/KMS).
        return {
            'board': 'arm64',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi3':
        return {
            'board': 'pi3',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm/v7',
        }
    if build_target == 'pi2':
        return {
            'board': 'pi2',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm/v7',
        }

    return {
        'board': 'x86',
        'base_image': BASE_IMAGE,
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


def get_uv_builder_context(
    service: str, board: str | None = None
) -> dict[str, Any]:
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

    # Pillow / pillow-heif build deps for legacy 32-bit Pi boards.
    # Pillow 11+ dropped armv7l manylinux wheels (its release notes
    # spell this out), and pillow-heif likewise ships only x86_64 /
    # aarch64 wheels. uv's resolution on a pi2 / pi3 image build
    # therefore falls back to sdist, which requires the system
    # headers below to compile from source. Without them, the
    # ``uv sync`` step in ``uv-builder`` would fail at gcc on every
    # 32-bit Pi build.
    #
    # 64-bit boards (pi4-64 / pi5 / x86) and the test image
    # (always built on amd64 in CI) get binary wheels and don't
    # need any of this — adding the deps unconditionally would
    # waste ~70 MB of layer space on every image we don't need
    # them in.
    armv7_boards = {'pi2', 'pi3'}
    if uv_group == 'server' and board in armv7_boards:
        builder_extra_apt.extend(
            [
                # Pillow's documented build-time deps. ``-dev``
                # variants ship the headers compile from source
                # needs; the runtime ``.so`` files are pulled in
                # by base_apt_dependencies via libjpeg62-turbo,
                # libfreetype6, etc., or transitively by
                # cec-utils / ffmpeg.
                'libjpeg62-turbo-dev',
                'libfreetype-dev',
                'liblcms2-dev',
                'libopenjp2-7-dev',
                'libtiff-dev',
                'libwebp-dev',
                'zlib1g-dev',
                # pillow-heif: libheif-dev exposes the libheif1
                # API headers so the cython binding can compile.
                # libheif1 itself is already in
                # base_apt_dependencies (runtime).
                'libheif-dev',
            ]
        )

    return {
        'uv_group': uv_group,
        'builder_extra_apt': builder_extra_apt,
        'uv_system_site_packages': service in {'viewer', 'test'},
    }


def get_test_context() -> dict[str, Any]:
    # Playwright's `playwright install --with-deps chromium` pulls the
    # apt packages it actually needs (the list shifts between Chromium
    # builds, so leaving it to playwright avoids stale apt names that
    # Debian eventually retires). The test image otherwise inherits all
    # tooling from the base image (curl, ca-certificates, etc.), so the
    # apt list here is empty.
    return {
        'apt_dependencies': [],
    }


def get_viewer_context(board: str, target_platform: str) -> dict[str, Any]:
    releases_url = f'{GITHUB_REPO_URL}/releases/download'

    is_qt6 = board in ['pi5', 'pi4-64', 'x86', 'arm64']

    # Qt version is only relevant for the Qt 5 path: pi2/pi3 pull the
    # cross-built Qt 5 toolchain tarball at build time. Qt 5 is frozen
    # for these boards, so the toolchain stays pinned to the
    # WebView-v2026.04.1 release indefinitely. Qt 6 boards install Qt
    # straight from Debian apt (qt6-*-dev in viewer_extra_apt below).
    qt_version = '6.4.2' if is_qt6 else '5.15.14'
    qt_major_version = qt_version.split('.')[0]
    qt5_toolchain_url = f'{releases_url}/WebView-v2026.04.1'

    # Viewer-only apt deps. The shared runtime set (cec-utils, curl,
    # ffmpeg, git, libcec7, procps, psmisc, python-is-python3,
    # python3-gi, python3-pip, python3-setuptools, sqlite3, sudo,
    # plus libraspberrypi0 on 32-bit Pi boards) is installed by
    # Dockerfile.base.j2 in a layer that server (and test) also use,
    # so it dedups across images. Anything listed here is unique to
    # the viewer image.
    #
    # The list below is the still-being-trimmed remainder of runtime
    # libs the WebView binary links against; expect more to fall off
    # as ldd-driven cleanup continues. (Qt itself is built in a
    # multi-stage builder inside Dockerfile.viewer.j2, not installed
    # from apt at runtime — except on Qt 6 boards where qt6-*-dev
    # below also provides the runtime libs.)
    #
    # X11/XCB packages are intentionally absent. Qt5 boards (pi2/pi3)
    # use a custom -no-xcb -no-xcb-xlib -qpa eglfs build of the WebView
    # (see webview/build_qt5.sh) and run under QT_QPA_PLATFORM=linuxfb
    # straight on KMS/DRM, with mpv on --vo=drm. Qt6 boards (pi4-64,
    # pi5, x86, arm64) run the viewer under `cage` (a kiosk wlroots
    # compositor) with QT_QPA_PLATFORM=wayland and mpv on --vo=gpu
    # --gpu-context=wayland — no X code path on either track. The
    # cage + qt6-wayland pair is added to the Qt6 apt extension below.
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
        'libxkbcommon0',
        # The prebuilt WebView binary dlopens libharfbuzz-subset.so.0
        # (Chromium's font-subsetting path). It's pulled in
        # transitively by qt6-webengine-dev on Qt6 boards but nothing
        # on Qt5 brings it, so the lib was simply missing on pi2/pi3
        # in production (pre-existing bug, not a regression here).
        # 200 KB, easier to just install everywhere than gate on board.
        'libharfbuzz-subset0',
        'python3-netifaces',
        'fonts-wqy-zenhei',
    ]

    if is_qt6:
        # All Qt6 boards (pi4-64, pi5, x86, arm64) run the viewer
        # under `cage`, a wlroots kiosk compositor, with mpv on
        # --vo=gpu --gpu-context=wayland for video. See
        # MPVMediaPlayer.play in src/anthias_viewer/media_player.py
        # and bin/start_viewer.sh for the runtime wiring. VLC is
        # deliberately *not* installed: MediaPlayerProxy routes Qt6
        # boards to MPVMediaPlayer, so VLC would just be ~80–100 MB
        # of dead weight here. qt6-wayland is the Qt platform plugin
        # the viewer loads to render into cage's surface.
        viewer_extra_apt_dependencies.extend(
            [
                'cage',
                'mpv',
                'qt6-base-dev',
                'qt6-wayland',
                'qt6-webengine-dev',
                'qt6-image-formats-plugins',
            ]
        )

        if board == 'x86':
            # va-driver-all is a Debian metapackage that pulls in
            # intel-media-va-driver (modern Intel iHD), i965-va-driver
            # (older Intel), and mesa-va-drivers (Gallium / AMD
            # radeonsi etc.), so the image runs on any x86 GPU without
            # per-vendor build variants — mpv's --hwdec=auto-safe
            # picks whichever VAAPI driver matches the device at
            # runtime.
            #
            # Deliberately NOT shipped on arm64: Rockchip
            # (rkvdec/hantro), Allwinner (cedrus), and Amlogic
            # (meson-vdec) all expose hardware decode via V4L2 M2M /
            # request API, not VAAPI; mesa-va-drivers only covers
            # radeonsi/nouveau/etc., so on those SoCs va-driver-all
            # would just be dead weight. Per-SoC hwdec for ARM SBCs
            # is a Tier-2 follow-up.
            viewer_extra_apt_dependencies.extend(
                [
                    'va-driver-all',
                ]
            )
    else:
        # libraspberrypi0 already comes in via base_apt_dependencies on
        # 32-bit Pi boards (see __main__.py), so it's deliberately not
        # repeated here. libssl1.1 is gone in trixie; the rebuilt Qt 5
        # webview archive links against libssl3 from the base image.
        # libgst-dev / libsqlite0-dev / libsrtp0-dev were dropped in
        # trixie — libsqlite3-dev and libsrtp2-dev are already in the
        # main viewer apt list above; libgstreamer1.0-dev is Qt 5-only
        # and is added in the extend() below. VLC is Qt5-only because
        # MediaPlayerProxy only routes pi2/pi3 to VLCMediaPlayer.
        viewer_extra_apt_dependencies.extend(
            [
                'libgstreamer1.0-dev',
                'qt5-image-formats-plugins',
                'vlc',
            ]
        )

    return {
        'viewer_extra_apt_dependencies': viewer_extra_apt_dependencies,
        'qt_version': qt_version,
        'qt_major_version': qt_major_version,
        'qt5_toolchain_url': qt5_toolchain_url,
        'is_qt6': is_qt6,
        'artifact_board': board,
    }
