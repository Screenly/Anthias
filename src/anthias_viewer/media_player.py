import logging
import os
import re
import signal
import subprocess
import sys
from typing import Any, ClassVar
from urllib.parse import quote

from anthias_common.board import ARM64_DEVICE_TYPES
from anthias_common.device_helper import get_device_type
from anthias_common.utils import clamp_screen_rotation
from anthias_server.settings import settings


# Lazy import for the pydbus proxy: the viewer service hands
# MPVMediaPlayer the same ``browser_bus`` object it uses for
# loadPage / loadImage (created in src/anthias_viewer/__init__.py
# during load_browser()). Tests inject a mock; importing pydbus at
# module load time would force every test to have pydbus available
# even when only exercising GstFbdevMediaPlayer.
_browser_bus: Any = None

# Optional wrapper that runs a ``browser_bus`` call and, if the webview
# died mid-call (a D-Bus "service gone" error), reaps + respawns it and
# retries the call once. Injected from anthias_viewer/__init__.py
# (``_send_to_webview``); the viewer and the media player share the same
# AnthiasViewer process, so a video-play D-Bus call hitting a crashed
# webview must self-heal exactly like loadImage/loadPage does (#3012),
# rather than logging ERROR and leaving the screen dark until the next
# rotation (Sentry ANTHIAS-1A). Left None in tests / standalone use, in
# which case calls run directly.
_send_to_webview: Any = None


def set_browser_bus(bus: Any) -> None:
    """Inject the AnthiasViewer D-Bus proxy.

    Called from ``anthias_viewer/__init__.py`` after the webview's
    ``Anthias service start`` handshake. ``MPVMediaPlayer`` reads
    this module-global on every play() / stop() so a webview crash +
    re-launch (with a fresh ``browser_bus`` proxy) can re-inject
    without rebuilding the media player.
    """
    global _browser_bus
    _browser_bus = bus


def get_browser_bus() -> Any:
    return _browser_bus


def set_send_to_webview(fn: Any) -> None:
    """Inject the webview-gone-aware call wrapper (see ``_send_to_webview``
    in anthias_viewer/__init__.py). Injected from setup() alongside
    ``set_browser_bus``."""
    global _send_to_webview
    _send_to_webview = fn


def _call_webview(send: Any) -> None:
    """Run ``send`` through the injected respawn-on-death wrapper when
    available, else call it directly. A webview-gone error is handled
    (respawn + retry) inside the wrapper; anything else propagates to
    the caller's own error handling."""
    if _send_to_webview is not None:
        _send_to_webview(send)
    else:
        send()


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
        'arm64: using the default audio device for HDMI audio '
        '(resolves to the PulseAudio default sink — see '
        'start_pulseaudio in bin/start_viewer.sh). If audio is '
        'silent, check `pactl list short sinks` in the viewer '
        'container. Registered ALSA cards:\n%s',
        listing,
    )


def get_alsa_audio_device() -> str:
    # device_helper.get_device_type() reads /proc/device-tree/model and
    # falls back to 'pi1' for any aarch64 host whose model line isn't a
    # Pi regex match (Rock Pi, Orange Pi, Banana Pi, …). The Pi-firmware
    # ALSA card names below (vc4hdmi*, "Headphones") don't exist on
    # those boards, so route via DEVICE_TYPE env first and only fall
    # through to the Pi-name dispatch when we're actually on a Pi.
    if os.environ.get('DEVICE_TYPE') in ARM64_DEVICE_TYPES:
        # No portable per-SoC HDMI card name across Rockchip /
        # Allwinner / Amlogic, so defer to the `default` device —
        # which VideoView::resolveAlsaDevice resolves to the
        # PulseAudio default sink (the daemon start_viewer.sh runs;
        # Debian's Qt 6 Multimedia only has a PulseAudio backend).
        # Log the chosen device at INFO once per process so a
        # silent-HDMI report carries enough breadcrumbs to debug
        # from journalctl alone.
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


def _marshal_dbus_options(options: dict[str, Any]) -> dict[str, Any]:
    """Wrap each value as a ``GLib.Variant`` for pydbus.

    AnthiasViewer's ``playVideo`` slot is declared with
    ``QVariantMap`` (Qt's ``a{sv}`` D-Bus signature) so the dict
    values are *variant-typed* across the wire. pydbus refuses to
    auto-coerce a plain Python scalar to ``GLib.Variant`` ("Expected
    GLib.Variant, but got str") — the wrap has to happen on the
    Python side. The variant signature is picked by Python type so a
    future int / bool option doesn't silently fail at
    ``GLib.Variant('s', 90)`` time (review of PR #2905 caught the
    prior string-only marshal as a footgun). Tests monkeypatch this
    function to the identity so they can assert on the plain dict.
    """
    from gi.repository import GLib

    def variant_for(value: Any) -> Any:
        if isinstance(value, bool):
            return GLib.Variant('b', value)
        if isinstance(value, int):
            return GLib.Variant('i', value)
        if isinstance(value, float):
            return GLib.Variant('d', value)
        return GLib.Variant('s', str(value))

    return {key: variant_for(value) for key, value in options.items()}


def _build_video_options(uri: str) -> dict[str, Any]:
    """Build the per-file option dict sent over D-Bus to AnthiasViewer.

    Qt 6.5 dropped the upstream gstreamer media backend, so the
    runtime path is QtMultimedia → libavcodec (the ffmpeg-backed
    plugin). The +rpt1 ``libav*`` packages pinned in
    ``docker/_rpt1-ffmpeg-pin.j2`` carry ``--enable-v4l2-request``
    / ``--enable-v4l2-m2m``, so libavcodec engages the Pi-family
    hardware decoders automatically — the application no longer
    dispatches per-codec hwdec. The options dict shrinks to:

    * ``audio-device`` — ALSA device name. C++ side strips the
      ``alsa/`` prefix and extracts the ``CARD=<name>`` segment
      to look up the matching ``QAudioDevice``.
    * ``video-rotate`` — Pi 4 only. Cage / wayland boards inherit
      the transform from wlr-randr at the compositor level;
      sending ``video-rotate`` on top would double-rotate. Sent
      as a Python ``int`` (was ``str`` before — the type-aware
      ``_marshal_dbus_options`` now serialises both correctly).

    The ``uri`` argument is kept on the signature for symmetry
    with the libmpv era (where it fed ffprobe). It's no longer
    read because libavcodec handles codec probing internally —
    but a future codec-specific tuning may re-introduce it.
    """
    del uri  # see docstring; kept for signature compatibility.

    options: dict[str, Any] = {
        'audio-device': f'alsa/{get_alsa_audio_device()}',
    }

    # No per-video rotation here. Every Qt6 board now rotates at the
    # platform layer and the QGraphicsVideoItem inherits the transform:
    # cage/wlroots (x86) via wlr-randr (issue #2856) and eglfs (pi4-64)
    # via QT_QPA_EGLFS_ROTATION (set in _build_webview_env). Sending
    # ``video-rotate`` on top of either would double-rotate the frames.
    # linuxfb boards (pi1/2/3) apply rotation through the GStreamer
    # ``videoflip`` element in GstFbdevMediaPlayer instead.
    return options


class MPVMediaPlayer(MediaPlayer):
    def __init__(self) -> None:
        MediaPlayer.__init__(self)
        self.uri: str = ''
        # No mpv subprocess any more — playback runs inside
        # AnthiasViewer via QtMultimedia (``QMediaPlayer`` +
        # ``QGraphicsVideoItem``), reached over D-Bus. Track local
        # playback state so ``is_playing()`` (called only by tests
        # today; the asset_loop sleeps for ``duration``) can still
        # answer without a D-Bus round-trip.
        self._playing: bool = False

    def set_asset(self, uri: str, duration: int | str) -> None:
        self.uri = uri

    def play(self) -> None:
        # Re-read settings each play so the audio_output dropdown
        # takes effect without a viewer restart, matching the prior
        # subprocess path and GstFbdevMediaPlayer.
        settings.load()

        options = _build_video_options(self.uri)

        bus = get_browser_bus()
        if bus is None:
            logging.error(
                'MPVMediaPlayer.play: AnthiasViewer D-Bus proxy not '
                'set — call set_browser_bus() after the webview '
                'handshake (src/anthias_viewer/__init__.py).'
            )
            return

        try:
            # Route through the respawn-on-death wrapper: a webview
            # that crashed mid-playback is reaped + respawned and the
            # playVideo retried, instead of logging ERROR and leaving
            # the screen dark (Sentry ANTHIAS-1A). A non-webview-gone
            # error still surfaces here.
            _call_webview(
                lambda: bus.playVideo(self.uri, _marshal_dbus_options(options))
            )
            self._playing = True
        except Exception as exc:
            # pydbus surfaces transport / signature errors as
            # generic exceptions. Log + clear local state so a
            # transient AnthiasViewer crash doesn't leave the
            # player thinking a video is on screen.
            logging.error('MPVMediaPlayer.play failed: %s', exc)
            self._playing = False

    def stop(self) -> None:
        self._playing = False
        bus = get_browser_bus()
        if bus is None:
            return
        try:
            _call_webview(lambda: bus.stopVideo())
        except Exception as exc:
            logging.error('MPVMediaPlayer.stop failed: %s', exc)

    def is_playing(self) -> bool:
        return self._playing


# ioctl + offsets for struct fb_var_screeninfo (linux/fb.h): __u32
# xres, yres, xres_virtual, yres_virtual, xoffset, yoffset,
# bits_per_pixel, … — xres/yres at offsets 0/4, bits_per_pixel at 24.
_FBIOGET_VSCREENINFO = 0x4600
_FB_VSCREENINFO_LEN = 160


def _fb_geometry(
    fb_device: str = '/dev/fb0',
    fb_sys: str = '/sys/class/graphics/fb0',
) -> tuple[int, int, str]:
    """Read the *visible* framebuffer resolution + GStreamer format.

    Returns (width, height, gst_format). 16bpp → RGB16 (the Pi vc4 fbcon
    rgb565 default), 32bpp → BGRx. The format pins the ``v4l2convert``
    (bcm2835 ISP) output so the hardware color-convert lands in the
    framebuffer's pixel layout.

    The resolution comes from the ``FBIOGET_VSCREENINFO`` ioctl —
    ``varinfo.xres``/``yres``, the exact fields ``fbdevsink`` uses for
    its centering/cropping math. The sysfs ``virtual_size`` node used
    previously reports ``xres_virtual``/``yres_virtual``, which can be
    larger than the scanned-out mode (panning / double-buffer
    configs); scaling to the virtual size on such a device paints
    mostly off-screen. Falls back to sysfs, then to 1920x1080/RGB16,
    so playback degrades rather than crashes when the ioctl is
    unavailable (e.g. unit tests on a host with no /dev/fb0).
    """
    try:
        import fcntl
        import struct

        with open(fb_device, 'rb') as fb:
            info = bytearray(_FB_VSCREENINFO_LEN)
            fcntl.ioctl(fb, _FBIOGET_VSCREENINFO, info)
        width, height = struct.unpack_from('=2I', info, 0)
        bpp = struct.unpack_from('=I', info, 24)[0]
        if width > 0 and height > 0 and bpp > 0:
            return width, height, 'BGRx' if bpp >= 32 else 'RGB16'
    except OSError:
        pass

    width, height = 1920, 1080
    try:
        with open(f'{fb_sys}/virtual_size') as handle:
            width, height = (int(x) for x in handle.read().strip().split(','))
    except (OSError, ValueError):
        pass
    bpp = 16
    try:
        with open(f'{fb_sys}/bits_per_pixel') as handle:
            bpp = int(handle.read().strip())
    except (OSError, ValueError):
        pass
    return width, height, 'BGRx' if bpp >= 32 else 'RGB16'


# A bare URI scheme: ``http``, ``https``, ``file``, ``rtsp`` …
_URI_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://')


def _as_gst_uri(uri: str) -> str:
    """Coerce an asset URI into one ``playbin`` accepts.

    Local assets store a bare absolute path
    (``settings['assetdir']/<asset_id>`` — see the API serializers), and
    ``playbin``'s ``uri`` property rejects anything without a scheme, so
    a bare path is wrapped as ``file://`` (``quote`` so a path with
    spaces stays a valid URI; ``/`` is left intact). A URI that already
    carries a scheme (``http(s)://`` streaming, ``file://``) is passed
    through unchanged. Without this, ``gst-launch ... uri=/data/...``
    fails with "Invalid URI" and the clip black-screens — the same
    failure this player exists to fix.
    """
    if _URI_SCHEME_RE.match(uri):
        return uri
    return 'file://' + quote(uri)


class GstFbdevMediaPlayer(MediaPlayer):
    """Pi 1/2/3 (Qt5 linuxfb) video player: GStreamer HW pipeline → fb.

    Spawns the ``anthias_viewer.gst_fbdev_player`` helper, which runs a
    ``playbin`` with a custom video sink that hardware-decodes through
    the board's V4L2 M2M decoder (``v4l2h264dec`` → /dev/video10,
    bcm2835-codec; auto-selected by decodebin at PRIMARY rank),
    hardware scales + color-converts through the bcm2835 ISP
    (``v4l2convert``), and paints straight to the framebuffer
    (``fbdevsink`` → /dev/fb0). fbdev needs no DRM master / X /
    Wayland — exactly what a bare uid-1000 viewer with no compositor
    cannot acquire (the python viewer holds card0's DRM master, so VLC's
    ``kms`` vout and mpv's ``--vo=drm`` both fail with EBUSY/EPERM here).

    This restores hardware-decoded video on these boards after #1980
    (the Bookworm upgrade) dropped the Broadcom ``mmal_vout`` VLC path —
    mmal did HW decode *and* HW scale/convert/scanout with no DRM master,
    and once it was gone VLC was left with only compositor/DRM outputs
    that render to nowhere on linuxfb. The VPU + ISP this pipeline drives
    is the same silicon mmal used: on a Pi 3 it sustains 1080p30 → rgb565
    at ~40 fps with zero dropped frames, where a CPU color-convert path
    (ffmpeg → fbdev) managed only ~6 fps because swscale's YUV→rgb565 has
    no NEON path and CPU scaling is unaccelerated. Documented in
    docs/board-enablement.md.

    The helper (rather than ``gst-launch-1.0`` in a bash relaunch
    loop, the original #2972 shape) exists for issue #2987: looping by
    re-spawning the whole pipeline froze the last frame for seconds
    per iteration while the slot's fixed duration kept ticking, and
    the fb-sized caps it forced stretched portrait videos. The helper
    loops gaplessly in-process (playbin ``about-to-finish``) and pins
    aspect-fit dimensions discovered from the decoder's CAPS event —
    see gst_fbdev_player.py for the full rationale.
    """

    def __init__(self) -> None:
        MediaPlayer.__init__(self)
        self.uri: str = ''
        self._proc: subprocess.Popen[bytes] | None = None
        self._fb_w, self._fb_h, self._fb_fmt = _fb_geometry()

    def set_asset(self, uri: str, duration: int | str) -> None:
        del duration  # the asset_loop owns the on-screen duration
        self.uri = uri
        settings.load()

    def _build_command(self) -> list[str]:
        # The helper is executed by path, not ``-m`` — running it as a
        # module would import the ``anthias_viewer`` package __init__
        # (Django settings, redis, D-Bus) in the child, which costs
        # seconds on a Pi 3 and would eat into the slot the same way
        # the old per-loop gst-launch respawn did.
        helper = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'gst_fbdev_player.py',
        )
        return [
            sys.executable,
            helper,
            '--uri',
            _as_gst_uri(self.uri),
            '--fb-width',
            str(self._fb_w),
            '--fb-height',
            str(self._fb_h),
            '--fb-format',
            self._fb_fmt,
            '--rotation',
            str(_screen_rotation()),
            '--audio-device',
            get_alsa_audio_device(),
        ]

    def play(self) -> None:
        self.stop()  # never leave a previous pipeline holding the fb
        argv = self._build_command()
        logging.info('GstFbdev play: %s', ' '.join(argv))
        # The helper loops the clip for the whole on-screen slot (the
        # asset_loop sleeps for ``duration`` then stop()s us) and exits
        # non-zero on a pipeline error so a persistent failure doesn't
        # spin. start_new_session puts it in its own process group so
        # stop() kills the GStreamer threads with it.
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                # Inherit stderr (not DEVNULL): the helper logs there,
                # so a device-side failure (caps negotiation, missing
                # /dev/fb0, decoder busy) surfaces in the viewer's
                # container log instead of vanishing — the
                # silent-failure mode this player exists to escape.
                stderr=None,
                start_new_session=True,
            )
        except OSError as exc:
            logging.error('GstFbdev: failed to spawn player: %s', exc)
            self._proc = None

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            try:
                pgid = os.getpgid(self._proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # SIGTERM didn't take in time — SIGKILL the group, then
                # wait() to reap the bash leader so we don't leak a
                # zombie before dropping the handle.
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    self._proc.wait(timeout=3)
                except (
                    ProcessLookupError,
                    OSError,
                    subprocess.TimeoutExpired,
                ):
                    pass
            except (ProcessLookupError, OSError):
                pass
        self._proc = None

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class MediaPlayerProxy:
    INSTANCE: ClassVar[MediaPlayer | None] = None

    @classmethod
    def get_instance(cls) -> MediaPlayer:
        if cls.INSTANCE is None:
            # The Qt5 linuxfb boards (pi1/pi2/pi3) use
            # GstFbdevMediaPlayer: HW decode + HW scale/convert via V4L2
            # (bcm2835 codec + ISP) → fbdevsink. Every other board is
            # Qt6 + in-process QtMultimedia (MPVMediaPlayer over D-Bus).
            # Pi 4 is intentionally NOT in the dispatch list: it reports
            # device_type 'pi4' (eglfs/Qt6), so it falls straight to the
            # else → mpv, even if DEVICE_TYPE is missing/mis-set.
            #
            # force_mpv (legacy name — the player it selects is
            # MPVMediaPlayer, which despite the name drives in-process
            # QtMultimedia playback over D-Bus, no mpv binary) overrides
            # get_device_type() with the authoritative baked DEVICE_TYPE
            # whenever the model string alone would route a Qt6 image to
            # the Qt5 Gst player:
            #   * pi4-64 — the 64-bit Pi 4 image (Qt6/eglfs).
            #   * pi3-64 — the 64-bit Pi 3 image (Qt6/eglfs). The model
            #     node still reads "Raspberry Pi 3", so get_device_type()
            #     returns 'pi3' and would otherwise pick the armhf/Qt5
            #     Gst player; the pi3-64 image has no GStreamer fbdev
            #     stack, so the env override is what keeps it on the
            #     QtMultimedia path.
            #   * arm64 / generic-arm64 — non-Pi aarch64 SBCs whose
            #     unreadable/unmatched model node makes get_device_type()
            #     fall back to 'pi1' (``generic-arm64`` covers pre-rename
            #     images).
            device_env = os.environ.get('DEVICE_TYPE')
            force_mpv = device_env in (
                'pi4-64',
                'pi3-64',
                'arm64',
                'generic-arm64',
            )
            if get_device_type() in ['pi1', 'pi2', 'pi3'] and not force_mpv:
                cls.INSTANCE = GstFbdevMediaPlayer()
            else:
                cls.INSTANCE = MPVMediaPlayer()

        return cls.INSTANCE

    @classmethod
    def reset(cls) -> None:
        """Drop the cached player so the next ``get_instance`` rebuilds it.

        Both players now re-read ``screen_rotation`` per play()
        (GstFbdevMediaPlayer composes the ``videoflip`` element in
        _video_sink; MPVMediaPlayer sends it over D-Bus), so a
        rotation change from the Settings page takes effect on the next
        play without a rebuild — but calling reset() is cheap and
        harmless, and it also re-probes the framebuffer geometry.
        """
        if cls.INSTANCE is not None:
            try:
                cls.INSTANCE.stop()
            except Exception as exc:
                logging.debug('reset(): stop() raised: %s', exc)
        cls.INSTANCE = None
