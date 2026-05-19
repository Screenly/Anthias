from typing import Any

from jinja2 import Environment, FileSystemLoader

from tools.image_builder.constants import BASE_IMAGE


def get_build_parameters(build_target: str) -> dict[str, Any]:
    # Every surviving board now lands on vanilla `debian:trixie` with
    # Qt 6 from Debian apt. Pi 2 (armhf) is the only 32-bit target
    # left and adds the Raspberry Pi / Raspbian apt sources at
    # image-build time for `libraspberrypi0` (see Dockerfile.base.j2);
    # 64-bit and x86 builds need nothing Pi-specific at all.
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
        # Pi 3 dropped its Qt 5 armhf cross-build in Phase 2 of #2906
        # and now ships an arm64 image. Existing 32-bit Pi 3
        # deployments won't auto-upgrade — they require a reflash to
        # 64-bit Raspberry Pi OS. The runtime wiring mirrors pi4-64
        # (eglfs + libraspberrypi0).
        return {
            'board': 'pi3',
            'base_image': BASE_IMAGE,
            'target_platform': 'linux/arm64/v8',
        }
    if build_target == 'pi2':
        # Pi 2 stays armhf — the only 32-bit board left in the matrix.
        # Qt 6 comes from Debian Trixie's `qt6-*-dev` packages (the
        # same apt path the 64-bit boards use); the Cortex-A7 perf
        # envelope under Qt 6 WebEngine is the open Phase 2 risk and
        # is gated on real-hardware QA.
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

    # Pillow / pillow-heif build deps for the only 32-bit board left
    # (pi2). Pillow 11+ dropped armv7l manylinux wheels (its release
    # notes spell this out), and pillow-heif likewise ships only
    # x86_64 / aarch64 wheels. uv's resolution on a pi2 image build
    # therefore falls back to sdist, which requires the system
    # headers below to compile from source. Without them, the
    # ``uv sync`` step in ``uv-builder`` would fail at gcc.
    #
    # 64-bit boards (pi3 / pi4-64 / pi5 / arm64 / x86) and the test
    # image (always built on amd64 in CI) get binary wheels and
    # don't need any of this — adding the deps unconditionally
    # would waste ~70 MB of layer space on every image we don't
    # need them in.
    if uv_group == 'server' and board == 'pi2':
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
    # Viewer-only apt deps. The shared runtime set (cec-utils, curl,
    # ffmpeg, git, libcec7, procps, psmisc, python-is-python3,
    # python3-gi, python3-pip, python3-setuptools, sqlite3, sudo,
    # plus libraspberrypi0 on pi2 — the only armhf board left) is
    # installed by Dockerfile.base.j2 in a layer that server (and
    # test) also use, so it dedups across images. Anything listed
    # here is unique to the viewer image.
    #
    # Qt is installed from Debian Trixie's apt — `qt6-base-dev`,
    # `qt6-webengine-dev`, `qt6-multimedia-dev` — on every board.
    # The viewer image's multi-stage builder qmake's the in-tree
    # `src/anthias_webview/` source against those packages. The Qt 5
    # cross-compile toolchain that pi2/pi3 used through #2906 Phase 1
    # is gone (toolchain Dockerfile / `bin/rebuild_qt5_toolchain.sh`
    # / `WebView-v2026.04.1` release pin all deleted in Phase 2).
    #
    # X11/XCB packages are intentionally absent. Two display tracks
    # across the matrix (no X code path on either):
    #
    # * Pi2 / Pi3 / Pi4-64: Qt eglfs + KMS/EGL via Mesa. mpv talks
    #   straight to DRM/KMS (`--vo=gpu --gpu-context=drm`). Pi 4
    #   needs eglfs anyway because QtMultimedia's video pipeline
    #   wants an OpenGL context (#2904); pi2/pi3 inherit the same
    #   wiring because the same QtMultimedia code path runs there
    #   after the C++ AnthiasViewer port lands.
    # * Pi5 / x86 / arm64: cage (a kiosk wlroots compositor) with
    #   QT_QPA_PLATFORM=wayland and mpv --vo=gpu
    #   --gpu-context=wayland. The cage + qt6-wayland + wlr-randr
    #   triple is added to the per-board apt extension below.
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
        # transitively by qt6-webengine-dev, but pinning it explicitly
        # also documents the dlopen-not-DT_NEEDED nature so a future
        # apt cleanup pass doesn't drop it.
        'libharfbuzz-subset0',
        'python3-netifaces',
        'fonts-wqy-zenhei',
    ]

    # Shared Qt 6 runtime for every board. VideoView uses QMediaPlayer
    # + QGraphicsVideoItem from qt6-multimedia. Qt 6.5 dropped the
    # upstream gstreamer media backend, so Debian Trixie ships only
    # the ffmpeg-backed ``libffmpegmediaplugin.so`` in
    # ``/usr/lib/.../qt6/plugins/multimedia/`` — decode runs through
    # libavcodec directly. The +rpt1 ``ffmpeg`` / ``libav*`` packages
    # pinned in _rpt1-ffmpeg-pin.j2 carry ``--enable-v4l2-request`` +
    # ``--enable-v4l2-m2m`` on Pi/arm64, so libavcodec engages the
    # rpi-hevc-dec + bcm2835-codec hardware on Pi boards — no
    # gstreamer plugin set needed.
    viewer_extra_apt_dependencies.extend(
        [
            'libqt6multimedia6',
            'libqt6multimediawidgets6',
            'qt6-base-dev',
            'qt6-image-formats-plugins',
            'qt6-multimedia-dev',
            'qt6-webengine-dev',
        ]
    )

    if board in ('x86', 'arm64', 'pi5'):
        # cage is a kiosk wlroots compositor that talks straight to
        # KMS; qt6-wayland is the Qt platform plugin the viewer loads
        # to render into cage's surface; mpv talks to the same
        # Wayland socket via --vo=gpu --gpu-context=wayland (see
        # MPVMediaPlayer.play in src/anthias_viewer/media_player.py).
        # wlr-randr is how src/anthias_viewer/__init__.py applies the
        # Settings page's "screen rotation" knob — Qt's wayland QPA
        # has no rotation= equivalent, so the transform goes through
        # the compositor for both Qt and mpv consistently.
        #
        # Pi 2 / Pi 3 / Pi 4-64 are intentionally NOT on this path:
        # the V3D 6.0 (Pi 4) doesn't have the bandwidth to composite
        # cage on top of video, and V3D IV / V3D 4.0 on Pi 2 / Pi 3
        # has even less headroom. Those boards land on eglfs + mpv
        # --vo=gpu --gpu-context=drm — see bin/start_viewer.sh and
        # docker/Dockerfile.viewer.j2.
        viewer_extra_apt_dependencies.extend(
            [
                'cage',
                'qt6-wayland',
                'wlr-randr',
            ]
        )

    if board in ('pi2', 'pi3'):
        # Pi 2 / Pi 3 still ship the Python viewer (the C++
        # AnthiasViewer takes over the video path in #2906 Phase 3),
        # and ``MediaPlayerProxy`` in ``src/anthias_viewer/media_player.py``
        # routes those boards to ``VLCMediaPlayer``. Keep VLC and
        # the gstreamer header set installed so the Python viewer
        # boots; once Phase 3 lands they fall out alongside the
        # Python package itself in Phase 5.
        viewer_extra_apt_dependencies.extend(
            [
                'libgstreamer1.0-dev',
                'vlc',
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
        # Deliberately NOT shipped on arm64/Pi: Rockchip
        # (rkvdec/hantro), Allwinner (cedrus), Amlogic
        # (meson-vdec), and the Pi V3D all expose hardware decode
        # via V4L2 M2M / request API, not VAAPI; mesa-va-drivers
        # only covers radeonsi/nouveau/etc., so on those SoCs
        # va-driver-all would just be dead weight. Per-SoC hwdec
        # for those boards is a Tier-2 follow-up.
        viewer_extra_apt_dependencies.extend(
            [
                'va-driver-all',
            ]
        )

    return {
        'viewer_extra_apt_dependencies': viewer_extra_apt_dependencies,
        'artifact_board': board,
    }
