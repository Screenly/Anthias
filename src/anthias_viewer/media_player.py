import logging
import os
from typing import Any, ClassVar

from anthias_common.board import ARM64_DEVICE_TYPES
from anthias_common.device_helper import get_device_type
from anthias_common.utils import clamp_screen_rotation
from anthias_server.settings import settings

VIDEO_TIMEOUT = 20  # secs


# Lazy import for the pydbus proxy: the viewer service hands
# MPVMediaPlayer the same ``browser_bus`` object it uses for
# loadPage / loadImage (created in src/anthias_viewer/__init__.py
# during load_browser()). Tests inject a mock; importing pydbus at
# module load time would force every test to have pydbus available
# even when only exercising VLCMediaPlayer.
_browser_bus: Any = None


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
    if os.environ.get('DEVICE_TYPE') in ARM64_DEVICE_TYPES:
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
            # x86 fallback: ALSA card names vary across Intel/AMD/Nvidia
            # HDA chipsets, so there is no portable per-SoC name we can
            # hard-code. Defer to ALSA's `default` device and let
            # operators override via ~/.asoundrc (already bind-mounted
            # into the viewer container — see docker-compose.yml.tmpl),
            # mirroring the ARM64 path above.
            return 'default'


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
    device_type = os.environ.get('DEVICE_TYPE', '')

    options: dict[str, Any] = {
        'audio-device': f'alsa/{get_alsa_audio_device()}',
    }

    # Rotation: cage/wlroots boards rotate via wlr-randr (issue
    # #2856, wired in src/anthias_viewer/__init__.py) and Qt's
    # wayland QPA inherits the transform — passing video-rotate
    # on top would double-rotate. On Pi 4 (eglfs, no compositor)
    # Qt has no transform plumbing, so the video pipeline has to
    # apply the rotation itself (via QGraphicsVideoItem::setRotation
    # in VideoView). Sent as int so the C++ side parses it cleanly
    # via ``QVariant::toInt`` without a string round-trip.
    rotation = _screen_rotation()
    if rotation and device_type == 'pi4-64':
        options['video-rotate'] = rotation

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
        # subprocess path and VLCMediaPlayer.
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
            bus.playVideo(self.uri, _marshal_dbus_options(options))
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
            bus.stopVideo()
        except Exception as exc:
            logging.error('MPVMediaPlayer.stop failed: %s', exc)

    def is_playing(self) -> bool:
        return self._playing


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
            # Force MPV (over VLC) on the device_types that otherwise
            # match the Pi-name dispatch below:
            #
            #   * pi4-64 — Qt6 + linuxfb like pi5/x86, so VLC's
            #     GL/GLES2/XCB outputs have no parent window to draw
            #     into. MPV renders straight to KMS via --vo=drm.
            #   * arm64 / generic-arm64 —
            #     device_helper.get_device_type() falls back to
            #     'pi1' on any aarch64 host whose
            #     /proc/device-tree/model isn't a Pi regex match
            #     (Rock Pi, Orange Pi, Banana Pi, …); without this
            #     override they'd silently route to VLC, which has
            #     no working backend on those boards (no vc4 KMS,
            #     no XCB under cage). ``generic-arm64`` covers
            #     pre-rename images still in the wild.
            device_env = os.environ.get('DEVICE_TYPE')
            force_mpv = device_env in ('pi4-64', 'arm64', 'generic-arm64')
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
