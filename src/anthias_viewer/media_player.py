import logging
import os
import subprocess
from typing import IO, ClassVar

from anthias_common.device_helper import get_device_type
from anthias_common.utils import clamp_screen_rotation
from anthias_server.settings import settings

VIDEO_TIMEOUT = 20  # secs


def _screen_rotation() -> int:
    """Cardinal angle the operator selected on the Settings page.

    Thin wrapper around the shared clamp_screen_rotation() helper so
    both the viewer's QPA env composition and the video-player CLI
    flags read from a single coercion path.
    """
    try:
        raw = settings['screen_rotation']
    except KeyError:
        return 0
    return clamp_screen_rotation(raw)


# Last device that `_detect_hdmi_audio_device()` resolved to in this
# process. `get_alsa_audio_device()` runs on every play()/set_asset(),
# so we only emit INFO/WARNING when the result changes (transitions
# between "HDMI-A-1 connected", "HDMI-A-2 connected", and the fallback)
# and drop to DEBUG otherwise. None means "haven't detected yet" — the
# first call always logs at the higher level.
_last_detected_device: str | None = None


def _detect_hdmi_audio_device() -> str:
    """Auto-detect which HDMI port is connected on Pi4/Pi5.

    The vc4 DRM driver exposes both HDMI connectors as
    /sys/class/drm/<card>-HDMI-A-{1,2}/. The <card> prefix
    depends on probe order (typically card1 on Pi OS Bookworm/
    Trixie, but kernels/images may differ), so the directories
    are discovered by scanning /sys/class/drm rather than
    assumed.

    ALSA exposes the matching audio devices as vc4hdmi0
    (HDMI-A-1) and vc4hdmi1 (HDMI-A-2). The ALSA card name
    (vc4hdmiN) is independent of the DRM card index.

    Returns sysdefault:CARD=<vc4hdmiN> for the first connector
    whose status file reads "connected" (HDMI-A-1 preferred).
    sysdefault is preferred over default to bypass PulseAudio/
    dmix wrappers that can intercept the "default" device.

    Falls back to sysdefault:CARD=vc4hdmi0 when neither
    connector reports connected (display asleep, status files
    unavailable, or unexpected DRM layout).
    """
    try:
        entries = list(os.scandir('/sys/class/drm'))
    except OSError as exc:
        logging.debug('Could not scan /sys/class/drm: %s', exc)
        entries = []

    hdmi_to_alsa = {'HDMI-A-1': 'vc4hdmi0', 'HDMI-A-2': 'vc4hdmi1'}
    # Annotation is a string so it is not evaluated at import time —
    # `os.DirEntry[str]` is subscriptable on 3.9+, but quoting it keeps
    # this module loadable on any interpreter without depending on
    # PEP-585 runtime support.
    ports: 'list[tuple[str, os.DirEntry[str]]]' = []
    for entry in entries:
        for suffix in hdmi_to_alsa:
            if entry.name.endswith(suffix):
                ports.append((suffix, entry))
                break
    # Sort on the HDMI-A-N suffix (not the full entry name) so
    # HDMI-A-1 always wins over HDMI-A-2 when both are connected,
    # even if a non-vc4 DRM card lex-sorts ahead (e.g. card0-...).
    ports.sort(key=lambda pair: pair[0])

    detected_entry_name: str | None = None
    detected_card: str | None = None
    for suffix, entry in ports:
        card_name = hdmi_to_alsa[suffix]
        status_path = os.path.join(entry.path, 'status')
        try:
            with open(status_path) as f:
                if f.read().strip() == 'connected':
                    detected_entry_name = entry.name
                    detected_card = card_name
                    break
        except OSError as exc:
            logging.debug(
                'HDMI status read failed for %s: %s', status_path, exc
            )

    global _last_detected_device
    if detected_card is not None:
        device = f'sysdefault:CARD={detected_card}'
        if device != _last_detected_device:
            logging.info(
                'Detected connected HDMI: %s -> %s',
                detected_entry_name,
                device,
            )
        else:
            logging.debug(
                'Detected connected HDMI: %s -> %s',
                detected_entry_name,
                device,
            )
        _last_detected_device = device
        return device

    device = 'sysdefault:CARD=vc4hdmi0'
    if device != _last_detected_device:
        # First call, or we just lost a previously detected
        # connection — be loud so the cause is visible in logs.
        logging.warning(
            'No connected HDMI detected, falling back to %s', device
        )
    else:
        logging.debug('No connected HDMI detected, falling back to %s', device)
    _last_detected_device = device
    return device


# Once-per-process flag for _log_arm64_alsa_default_once() — we don't
# want every play() call repeating the same INFO line.
_arm64_alsa_logged = False


def _log_arm64_alsa_default_once() -> None:
    """Log the kernel's ALSA card listing so silent-HDMI reports are
    debuggable from journalctl alone. Reads /proc/asound/cards (always
    present when sound is registered) rather than shelling to
    `aplay -l` — the viewer image deliberately doesn't ship
    alsa-utils, so the subprocess form would always fall through to
    a "not found" error and surface no useful info.
    """
    global _arm64_alsa_logged
    if _arm64_alsa_logged:
        return
    _arm64_alsa_logged = True
    try:
        with open('/proc/asound/cards') as f:
            listing = f.read().strip() or '<no cards registered>'
    except OSError as exc:
        listing = f'<could not read /proc/asound/cards: {exc}>'
    logging.info(
        'arm64: using ALSA "default" device for HDMI audio. '
        'If audio is silent, override via ~/.asoundrc (bind-mounted '
        'into the viewer container — see docker-compose.yml.tmpl). '
        'Registered ALSA cards:\n%s',
        listing,
    )


def get_alsa_audio_device() -> str:
    # device_helper.get_device_type() reads /proc/device-tree/model and
    # falls back to 'pi1' for any aarch64 host whose model line isn't a
    # Pi regex match (Rock Pi, Orange Pi, Banana Pi, …). The Pi-firmware
    # ALSA card names below (vc4hdmi*, "Headphones") don't exist on
    # those boards, so route via DEVICE_TYPE env first and only fall
    # through to the Pi-name dispatch when we're actually on a Pi.
    if os.environ.get('DEVICE_TYPE') == 'arm64':
        # No portable per-SoC HDMI card name across Rockchip /
        # Allwinner / Amlogic, so defer to ALSA's `default` device.
        # Operators with a non-standard HDMI sink can override via
        # ~/.asoundrc (already bind-mounted into the viewer container
        # — see docker-compose.yml.tmpl). Log the chosen ALSA card
        # at INFO once per process so a silent-HDMI report carries
        # enough breadcrumbs to debug from journalctl alone (the
        # `aplay -l` enumeration also lands in the same log).
        _log_arm64_alsa_default_once()
        return 'default'

    device_type = get_device_type()
    if settings['audio_output'] == 'local':
        if device_type == 'pi5':
            return _detect_hdmi_audio_device()

        return 'plughw:CARD=Headphones'
    else:
        if device_type in ['pi4', 'pi5']:
            return _detect_hdmi_audio_device()
        elif device_type in ['pi1', 'pi2', 'pi3']:
            return 'sysdefault:CARD=vc4hdmi'
        else:
            return 'sysdefault:CARD=HID'


class MediaPlayer:
    def __init__(self) -> None:
        pass

    def set_asset(self, uri: str, duration: int | str) -> None:
        raise NotImplementedError

    def play(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError


# Per-codec hwdec preference on Pi, per-board:
#
#   Pi 4: H.264 → v4l2m2m-copy  (V3D V4L2 M2M decoder via
#                                bcm2835-codec, up to 1080p60)
#         HEVC  → drm-copy      (FFmpeg v4l2_request_hevc, up to
#                                4Kp60 via the dedicated HEVC block)
#
#   Pi 5: H.264 → auto-copy     (Hantro G1 silicon exists but mpv
#                                has no v4l2-request H.264 hwdec
#                                upstream; passing v4l2m2m-copy
#                                here would just log "Could not
#                                find a valid device" errors before
#                                silently SW-falling-back. The
#                                playback envelope (HEVC 4Kp60) means
#                                every asset normalised post-rollout
#                                lands as HEVC, so this branch only
#                                fires for legacy variants the
#                                re-render walker hasn't caught yet.)
#         HEVC  → drm-copy      (Hantro G2, up to 4Kp60. Requires
#                                `dtoverlay=vc4-kms-v3d,cma-512` in
#                                /boot/firmware/config.txt — the
#                                stock 64 MB CMA region can't fit
#                                a 4K HEVC dst buffer pool, and the
#                                kernel cmdline `cma=` route silently
#                                orphans the rpi-hevc-dec driver.)
#
#   generic-arm64 (Armbian SBCs — Rock Pi 4, Orange Pi, etc.):
#         H.264 → v4l2m2m-copy  (RK3399 Hantro via the v4l2m2m
#         HEVC  → v4l2m2m-copy   driver. mpv's --hwdec=help on the
#                                latest-generic-arm64 image lists
#                                both h264_v4l2m2m and hevc_v4l2m2m;
#                                on boards without a working driver
#                                mpv logs a warning and SW-falls-
#                                back at runtime.)
#
# `auto-copy` is the universal safe fallback when ffprobe can't
# read the codec (missing file, network URI we don't probe, etc.).
#
# An earlier revision did this with a Lua on_load hook, but
# video-codec-name is empty at every event mpv exposes to scripts
# before hwdec init (on_load, on_preloaded). ffprobing from Python
# at launch time is both simpler and the only thing that actually
# works.
_PI_HWDEC_BY_CODEC: dict[str, dict[str, str]] = {
    'pi4-64': {'h264': 'v4l2m2m-copy', 'hevc': 'drm-copy'},
    'pi5': {'hevc': 'drm-copy'},
    # Rock Pi 4 (RK3399, Radxa). Resolved at runtime from
    # /proc/device-tree/model when DEVICE_TYPE is ``arm64`` — see
    # ``_pi_hwdec_for_uri``. Both codecs use ``v4l2m2m-copy`` because
    # mpv's ``--hwdec=help`` on the arm64 image lists both
    # ``h264_v4l2m2m`` and ``hevc_v4l2m2m`` (live-confirmed during
    # this PR). The Hantro VPU is what the v4l2m2m driver exposes.
    # Not added for generic ``arm64`` because every other aarch64
    # SBC routed to that DEVICE_TYPE may lack a working v4l2m2m
    # driver, in which case mpv would log "Could not find a valid
    # device" on every play.
    'rockpi4': {'h264': 'v4l2m2m-copy', 'hevc': 'v4l2m2m-copy'},
}


def _probe_video_codec(uri: str) -> str:
    """Return the canonical lowercase video codec name for ``uri``.

    Empty string on probe failure (missing file, unreadable codec,
    ffprobe absent, etc.) — callers should then pick a safe
    fallback like ``auto-copy``. Short timeout because this runs
    synchronously before every mpv launch.
    """
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v',
                'error',
                '-select_streams',
                'v:0',
                '-show_entries',
                'stream=codec_name',
                '-of',
                'default=nw=1:nk=1',
                uri,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip().lower()
    except (subprocess.SubprocessError, OSError):
        return ''


def _pi_hwdec_for_uri(uri: str, device_type: str) -> str:
    """mpv --hwdec= value for ``uri`` on Pi 4 / Pi 5 / Rock Pi 4.

    Generic ``arm64`` / ``generic-arm64`` DEVICE_TYPEs get a
    runtime SoC probe — ``bin/install.sh`` writes the same arm64
    DEVICE_TYPE for every aarch64 SBC because most lack any HW
    decode path mpv can address, but a few (Rock Pi 4 / RK3399)
    do expose ``v4l2m2m`` for H.264 + HEVC. We upgrade the
    dispatch key here when ``/proc/device-tree/model`` identifies
    a known board; an unknown SBC stays on ``auto-copy``.
    """
    effective = device_type
    if device_type in _ARM64_DEVICE_TYPES:
        sub = _detect_arm64_subtype()
        if sub is not None:
            effective = sub
    board_map = _PI_HWDEC_BY_CODEC.get(effective, {})
    return board_map.get(_probe_video_codec(uri), 'auto-copy')


# DEVICE_TYPE values that trigger the SoC probe above. Mirrors
# ``anthias_server.playback_envelope._ARM64_KEYS`` — the two paths
# must agree on what triggers the probe or the viewer's hwdec
# choice and the server's envelope choice will drift.
_ARM64_DEVICE_TYPES = frozenset({'arm64', 'generic-arm64'})


def _detect_arm64_subtype() -> str | None:
    """Identify the SBC by reading ``/proc/device-tree/model``.

    Duplicated from ``playback_envelope._detect_arm64_subtype``
    because the viewer doesn't depend on the server package. Both
    implementations must stay in sync; the unit test in
    ``tests/test_playback_envelope.py`` asserts that the
    server-side function returns the same key as this one for a
    Rock Pi model string.
    """
    try:
        with open('/proc/device-tree/model', 'rb') as f:
            model = f.read().decode('utf-8', 'replace').strip('\x00 \n\t')
    except OSError:
        return None
    if 'rock pi 4' in model.lower():
        return 'rockpi4'
    return None


class MPVMediaPlayer(MediaPlayer):
    def __init__(self) -> None:
        MediaPlayer.__init__(self)
        self.process: subprocess.Popen[bytes] | None = None
        self.uri: str = ''

    def set_asset(self, uri: str, duration: int | str) -> None:
        self.uri = uri

    def play(self) -> None:
        # Re-read settings each play so the audio_output dropdown takes
        # effect without a viewer restart, matching VLCMediaPlayer.
        settings.load()

        # Pin to 1080p on Pi4-64/Pi5: mpv's default --drm-mode=preferred
        # reads the connector's EDID-preferred mode (4K on most modern
        # TVs) and runs CPU zimg upscale, which drops below real-time
        # on the A72. Software decode of 1080p H.264 fits 4 cores fine.
        # Pi 5 keeps the same tuning on the cage path — it doesn't
        # hurt, and mpv ignores --drm-mode under --vo=gpu
        # --gpu-context=wayland anyway.
        device_type = os.environ.get('DEVICE_TYPE', '')
        extra_args: list[str] = []
        if device_type == 'pi4-64':
            extra_args = [
                '--drm-mode=1920x1080@60',
                '--vd-lavc-threads=4',
            ]
        elif device_type == 'pi5':
            extra_args = ['--vd-lavc-threads=4']

        # Per-board VO selection:
        #
        # * x86 / arm64 / pi5 run under `cage` (a wlroots kiosk
        #   compositor — see bin/start_viewer.sh); cage holds DRM
        #   master, so --vo=drm is denied. mpv goes through the GL
        #   VO over a Wayland EGL context. Paired with
        #   --hwdec=auto-safe, VAAPI-capable iGPUs on x86 (Intel
        #   iHD/i965, AMD radeonsi, …) decode in hardware and hand
        #   frames to the GL context as DMA-BUFs via
        #   dmabuf-interop-gl; software decode still works via the
        #   same VO. Pi 5's V3D 7.1 has enough bandwidth to composite
        #   at the connector's native mode (typically 4K) on top of
        #   software-decoded video. arm64 is best-effort per SoC.
        #   --vo=dmabuf-wayland would skip the GL upload entirely
        #   but segfaults under cage (mpv 0.40 + wlroots-0.18 +
        #   libplacebo dies between hwdec init and file open).
        #
        # * Pi4-64 stays on Qt linuxfb (no compositor) with mpv's
        #   --vo=drm. The V3D 6.0 doesn't have the bandwidth to
        #   composite cage on top of software-decoded video at 4K
        #   (738 vo drops/30 s in testing). mpv's --vo=drm does its
        #   own DRM master juggling — briefly grabbing master,
        #   rendering, dropping back — which coexists with Qt
        #   linuxfb in a way that --vo=gpu --gpu-context=drm does
        #   not (Mesa GBM holds master persistently and contends
        #   with Qt's framebuffer use, manifesting as "Failed to
        #   acquire DRM master: Permission denied"). So Pi 4 stays
        #   on --vo=drm + --drm-mode=1920x1080@60 — the production
        #   path inherited from master.
        if device_type in ('x86', 'arm64', 'pi5'):
            vo_args = ['--vo=gpu', '--gpu-context=wayland']
        else:
            vo_args = ['--vo=drm']

        # Rotation: cage/wlroots is rotated via wlr-randr (issue
        # #2856, the wiring lives in src/anthias_viewer/__init__.py)
        # and mpv's wayland VO inherits the compositor transform
        # automatically — passing --video-rotate would double-rotate
        # there. On Pi 4 (linuxfb, no compositor) mpv has to apply
        # the transform itself.
        rotation = _screen_rotation()
        rotate_args: list[str] = []
        if rotation and device_type == 'pi4-64':
            rotate_args = [f'--video-rotate={rotation}']

        # Hwdec selection. Strategy summary:
        #
        # * x86 / arm64 → `--hwdec=auto-copy`. Picks vaapi-copy on
        #   Intel/AMD iGPUs (the only HW decode method mpv 0.40 ships
        #   with for x86 outside of NVIDIA-specific options); on
        #   arm64 there's nothing in auto-copy that matches
        #   Rockchip's V4L2 stateless decoder, so it falls back to
        #   software. arm64 HW decode via a vendor-tuned plugin is a
        #   Tier-2 follow-up.
        # * Pi 4 / Pi 5 → ffprobe the asset (~50 ms for a local
        #   file) and pick per-codec, because `auto-copy`'s upstream
        #   whitelist deliberately excludes v4l2m2m-copy (H.264 V3D
        #   M2M is the path Pi 4 needs). See _pi_hwdec_for_uri().
        if device_type in ('pi4-64', 'pi5'):
            hwdec_value = _pi_hwdec_for_uri(self.uri, device_type)
        else:
            hwdec_value = 'auto-copy'

        # ANTHIAS_DEBUG_DROPS=1: when set on the viewer container,
        # mpv's stdout/stderr go to a host-bound log instead of
        # /dev/null, *and* --no-terminal is dropped so mpv's normal
        # status line ("AV: 00:00:30 / ... Dropped: N") is emitted.
        # The log records hwdec-current / VO init banners plus
        # per-file drop counts so reviewers can validate the test
        # bed without rebuilding the image. Default (unset)
        # preserves the silent stdout/stderr=/dev/null behaviour.
        debug_drops = os.environ.get('ANTHIAS_DEBUG_DROPS') == '1'
        terminal_args = [] if debug_drops else ['--no-terminal']
        # Popen accepts either an int sentinel (DEVNULL / STDOUT) or
        # an already-opened binary IO stream for stdout/stderr. The
        # int | IO[bytes] union covers both.
        popen_stdout: int | IO[bytes]
        popen_stderr: int | IO[bytes]
        if debug_drops:
            log_fd = open('/data/.anthias/mpv.log', 'ab', buffering=0)
            log_fd.write(f'\n--- mpv launch {self.uri} ---\n'.encode())
            popen_stdout = log_fd
            popen_stderr = subprocess.STDOUT
        else:
            popen_stdout = subprocess.DEVNULL
            popen_stderr = subprocess.DEVNULL

        # ``--video-sync=display-resample`` overrides mpv 0.40's
        # default (``audio``) which syncs video to the audio clock
        # and drops VO frames when the two clocks drift. On every
        # board we tested (Pi 4, Pi 5, x86) the audio-clock default
        # produced 60–90% VO drops at 60 fps content even when the
        # decoder was healthy (mpv reports drops at the VO, not the
        # decoder). Digital signage cares about smooth video more
        # than sub-frame A/V sync; display-resample syncs video to
        # the display's refresh and resamples audio to match. Audio
        # resampling is cheap (a 2-channel resample takes <1% CPU)
        # and most signage clips have no audible content anyway.
        self.process = subprocess.Popen(
            [
                'mpv',
                *terminal_args,
                *vo_args,
                f'--hwdec={hwdec_value}',
                '--video-sync=display-resample',
                *extra_args,
                *rotate_args,
                f'--audio-device=alsa/{get_alsa_audio_device()}',
                '--',
                self.uri,
            ],
            stdout=popen_stdout,
            stderr=popen_stderr,
        )

    def stop(self) -> None:
        try:
            if self.process:
                self.process.terminate()
                self.process = None
        except Exception as e:
            logging.error(f'Exception in stop(): {e}')

    def is_playing(self) -> bool:
        if self.process:
            return self.process.poll() is None
        return False


class VLCMediaPlayer(MediaPlayer):
    def __init__(self) -> None:
        MediaPlayer.__init__(self)

        # Imported here so Qt6 boards (which route to MPVMediaPlayer
        # via MediaPlayerProxy) don't need libvlc available just to
        # load this module.
        import vlc

        self._vlc = vlc
        options = [f'--alsa-audio-device={get_alsa_audio_device()}']
        # Issue #2856. Pi 1/2/3 fall through to VLC and write directly
        # to the framebuffer — no compositor or KMS transform in the
        # path — so any screen rotation has to be applied inside the
        # pipeline. The transform filter is software-rotation but the
        # SD-class boards on this branch only play SD-class assets, so
        # the cost is bearable. VLC requires the filter to be in the
        # chain at instance-init time; if the operator changes rotation
        # from the UI, MediaPlayerProxy.reset() drops the singleton so
        # we re-init with the new option list on the next play.
        self._rotation = _screen_rotation()
        if self._rotation:
            options.extend(
                [
                    '--video-filter=transform',
                    f'--transform-type={self._rotation}',
                ]
            )
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    def set_asset(self, uri: str, duration: int | str) -> None:
        self.player.set_mrl(uri)
        settings.load()
        self.player.audio_output_device_set('alsa', get_alsa_audio_device())
        # Use synchronous parse() to pre-load file metadata before play() is
        # called. parse_with_options() is async and returns before metadata is
        # ready, which negates the pre-loading benefit and causes the same
        # startup gap we're trying to reduce.
        self.player.get_media().parse()

    def play(self) -> None:
        self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.get_state() in [
            self._vlc.State.Playing,
            self._vlc.State.Buffering,
            self._vlc.State.Opening,
        ]


class MediaPlayerProxy:
    INSTANCE: ClassVar[MediaPlayer | None] = None

    @classmethod
    def get_instance(cls) -> MediaPlayer:
        if cls.INSTANCE is None:
            # Force MPV (over VLC) on two device_types that otherwise
            # match the Pi-name dispatch below:
            #
            #   * pi4-64 — Qt6 + linuxfb like pi5/x86, so VLC's
            #     GL/GLES2/XCB outputs have no parent window to draw
            #     into. MPV renders straight to KMS via --vo=drm.
            #   * arm64 — device_helper.get_device_type() falls back
            #     to 'pi1' on any aarch64 host whose
            #     /proc/device-tree/model isn't a Pi regex match
            #     (Rock Pi, Orange Pi, Banana Pi, …); without this
            #     override they'd silently route to VLC, which has
            #     no working backend on those boards (no vc4 KMS,
            #     no XCB under cage).
            device_env = os.environ.get('DEVICE_TYPE')
            force_mpv = device_env in ('pi4-64', 'arm64')
            if (
                get_device_type() in ['pi1', 'pi2', 'pi3', 'pi4']
                and not force_mpv
            ):
                cls.INSTANCE = VLCMediaPlayer()
            else:
                cls.INSTANCE = MPVMediaPlayer()

        return cls.INSTANCE

    @classmethod
    def reset(cls) -> None:
        """Drop the cached player so the next ``get_instance`` rebuilds it.

        VLC bakes the transform filter into ``vlc.Instance(options)``
        at init time, so a rotation change from the Settings page only
        takes effect on the next play after we re-init. mpv re-reads
        rotation per play and is unaffected, but calling reset() is
        cheap and harmless either way.
        """
        if cls.INSTANCE is not None:
            try:
                cls.INSTANCE.stop()
            except Exception as exc:
                logging.debug('reset(): stop() raised: %s', exc)
        cls.INSTANCE = None
