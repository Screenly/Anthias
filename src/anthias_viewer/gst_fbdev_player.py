"""Looping GStreamer video player for the Qt5 linuxfb boards (pi1/2/3).

Spawned by ``GstFbdevMediaPlayer`` (media_player.py) as
``python3 -m anthias_viewer.gst_fbdev_player --uri <asset> ...`` and
killed (process group SIGTERM) when the asset's slot ends. It replaces
the previous ``bash -c 'while true; do gst-launch-1.0 ...; done'``
wrapper, which rebuilt the whole pipeline — decoder open, demux,
preroll — on every loop iteration. On a Pi 3 that teardown/rebuild
costs seconds per iteration (measured 0.4–1.7 s on the much faster
Pi 4), during which the last frame sits frozen on the framebuffer and
the slot's fixed ``duration`` keeps ticking, so operators saw clips
freeze, start late, and get cut off (issue #2987).

What lives in-process here instead:

* **Gapless looping** — playbin's ``about-to-finish`` signal re-sets
  the same URI so the next iteration is queued *before* the current
  one drains; no teardown, no preroll gap. A flushing seek-to-zero on
  EOS covers sources where ``about-to-finish`` can't pre-queue, and a
  full NULL→PLAYING restart is the last-resort fallback for
  non-seekable sources.

* **Aspect-fit scaling** — the previous pipeline forced the
  framebuffer's full WxH onto ``v4l2convert``, which scaled every
  video to fill the screen and parked the distortion in a
  ``pixel-aspect-ratio`` that ``fbdevsink`` ignores (it paints pixel-
  for-pixel) — portrait videos rendered stretched (issue #2987). The
  source dimensions aren't knowable up front (legacy assets carry no
  ffprobe metadata), so a pad probe on ``v4l2convert``'s sink pad
  intercepts the CAPS event — which always precedes buffers — reads
  the decoder's native dims + PAR, and pins the downstream capsfilter
  to the aspect-fit size with ``pixel-aspect-ratio=1/1`` before the
  converter fixates its output. The bcm2835 ISP then does the
  aspect-correct scale in hardware and ``fbdevsink`` centers the
  result on screen (its documented behaviour for smaller-than-fb
  frames).

* **Frame-rate capping** — ``videorate drop-only=true max-rate=30``
  ahead of the converter. The decode→ISP→memcpy chain sustains
  ~40 fps at 1080p on a Pi 3 (docs/board-enablement.md), so 50/60 fps
  content otherwise judders on irregular late-frame drops; dropping
  to an even half-cadence upstream both looks smoother and halves the
  ISP + framebuffer work. ≤30 fps content passes through untouched
  (``drop-only`` never duplicates).

* **Framebuffer clearing** — letterbox/pillarbox borders expose
  whatever the previous asset left on /dev/fb0, so the visible
  framebuffer is zeroed once at startup.

``gi``/GStreamer imports happen inside ``main()`` so the module stays
importable on dev hosts without PyGObject (the unit tests exercise the
pure helpers below).
"""

import argparse
import logging
import signal
import sys
from typing import Any

FB_DEVICE = '/dev/fb0'

# Ceiling for frames/second pushed into the ISP + framebuffer blit.
# The full HW chain measures ~40 fps at 1080p/rgb565 on a Pi 3
# (docs/board-enablement.md), so 50/60 fps sources are capped to an
# even half-cadence instead of juddering on irregular sync drops.
MAX_OUTPUT_FPS = 30

# Operator screen_rotation → GStreamer ``videoflip`` method. None for
# an unrotated panel (the common case) so the videoflip element is
# omitted entirely and the pipeline stays fully hardware.
GST_VIDEOFLIP_METHODS = {
    90: 'clockwise',
    180: 'rotate-180',
    270: 'counterclockwise',
}


def compute_fit_dims(
    src_width: int,
    src_height: int,
    par_n: int,
    par_d: int,
    fb_width: int,
    fb_height: int,
) -> tuple[int, int]:
    """Aspect-fit ``src`` into ``fb``, honouring the source PAR.

    Returns the largest WxH that fits inside the framebuffer while
    preserving the source's *display* aspect ratio (pixel dims × pixel
    aspect ratio — anamorphic sources advertise non-square pixels).
    Dimensions are rounded down to even values: the V4L2 converter's
    YUV input planes are chroma-subsampled, and odd output dims can
    fail the ISP's alignment requirements.
    """
    if min(src_width, src_height, fb_width, fb_height) <= 0:
        return fb_width, fb_height
    if par_n <= 0 or par_d <= 0:
        par_n = par_d = 1

    display_w = src_width * par_n
    display_h = src_height * par_d

    # Scale = min(fit-to-width, fit-to-height), applied to the display
    # aspect. Integer math until the final round so 1080/1920-style
    # ratios don't accumulate float error.
    if display_w * fb_height >= display_h * fb_width:
        width = fb_width
        height = round(fb_width * display_h / display_w)
    else:
        height = fb_height
        width = round(fb_height * display_w / display_h)

    width = max(2, width - (width % 2))
    height = max(2, height - (height % 2))
    return width, height


def build_fit_caps_string(
    fb_format: str, width: int | None = None, height: int | None = None
) -> str:
    """Caps for the post-convert capsfilter.

    Without dims (pre-probe) only the framebuffer pixel format and a
    square PAR are pinned — enough for the converter to colour-convert
    while negotiation settles. The CAPS probe then re-pins with the
    computed aspect-fit WxH. ``pixel-aspect-ratio=1/1`` is the load-
    bearing part: without it the converter satisfies a forced WxH by
    stashing the distortion in the PAR, which fbdevsink ignores.
    """
    parts = [f'video/x-raw,format={fb_format}']
    if width is not None and height is not None:
        parts.append(f'width={width},height={height}')
    parts.append('pixel-aspect-ratio=1/1')
    return ','.join(parts)


def clear_framebuffer(
    fb_device: str = FB_DEVICE,
    fb_sys: str = '/sys/class/graphics/fb0',
) -> bool:
    """Zero the visible framebuffer (black) — best-effort.

    Letterbox borders around an aspect-fit video are simply regions
    fbdevsink never touches, so whatever the previous asset painted
    would otherwise stay visible there for the whole slot.
    """
    try:
        with open(f'{fb_sys}/stride') as handle:
            stride = int(handle.read().strip())
        with open(f'{fb_sys}/virtual_size') as handle:
            _, height = (int(x) for x in handle.read().strip().split(','))
        with open(fb_device, 'wb') as fb:
            fb.write(b'\x00' * (stride * height))
        return True
    except (OSError, ValueError) as exc:
        logging.warning('could not clear %s: %s', fb_device, exc)
        return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='gst_fbdev_player',
        description='Looping GStreamer playbin → fbdev video player',
    )
    parser.add_argument('--uri', required=True)
    parser.add_argument('--fb-width', type=int, required=True)
    parser.add_argument('--fb-height', type=int, required=True)
    parser.add_argument('--fb-format', required=True)
    parser.add_argument(
        '--rotation',
        type=int,
        default=0,
        choices=sorted({0, *GST_VIDEOFLIP_METHODS}),
    )
    parser.add_argument('--audio-device', default='')
    parser.add_argument('--fb-device', default=FB_DEVICE)
    return parser.parse_args(argv)


def build_sink_description(args: argparse.Namespace) -> str:
    """gst-parse description for playbin's ``video-sink`` bin.

    ``videorate`` caps the frame rate (drop-only — never duplicates),
    ``videoflip`` is inserted only when the operator rotated the
    screen, ``v4l2convert`` (bcm2835 ISP) does the scale + colour
    convert in hardware, and the named capsfilter is the handle the
    CAPS probe re-pins with the aspect-fit dimensions.
    """
    parts = [f'videorate drop-only=true max-rate={MAX_OUTPUT_FPS}']
    flip = GST_VIDEOFLIP_METHODS.get(args.rotation)
    if flip:
        parts.append(f'videoflip method={flip}')
    parts.append('v4l2convert name=fit_convert')
    parts.append(
        f'capsfilter name=fit_caps '
        f'caps={build_fit_caps_string(args.fb_format)}'
    )
    parts.append(f'fbdevsink device={args.fb_device}')
    return ' ! '.join(parts)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format='gst_fbdev_player: %(levelname)s %(message)s',
    )
    args = parse_args(argv if argv is not None else sys.argv[1:])

    import gi

    gi.require_version('Gst', '1.0')
    from gi.repository import GLib, Gst

    Gst.init(None)

    clear_framebuffer(args.fb_device)

    playbin = Gst.ElementFactory.make('playbin')
    if playbin is None:
        logging.error('playbin element unavailable')
        return 1

    sink_description = build_sink_description(args)
    logging.info('video sink: %s', sink_description)
    try:
        video_sink = Gst.parse_bin_from_description(sink_description, True)
    except GLib.Error as exc:
        logging.error('could not build video sink: %s', exc)
        return 1

    convert = video_sink.get_by_name('fit_convert')
    fit_caps = video_sink.get_by_name('fit_caps')

    def on_convert_event(pad: Any, info: Any) -> Any:
        """Pin the capsfilter to aspect-fit dims from the CAPS event.

        The CAPS event always precedes buffers, and the converter only
        fixates its output when it processes this event — so setting
        the downstream capsfilter here happens early enough to steer
        the very first negotiation. Kept installed (not removed after
        the first hit) so a mid-stream renegotiation recomputes.
        """
        event = info.get_event()
        if event.type != Gst.EventType.CAPS:
            return Gst.PadProbeReturn.OK
        structure = event.parse_caps().get_structure(0)
        ok_w, src_w = structure.get_int('width')
        ok_h, src_h = structure.get_int('height')
        if not (ok_w and ok_h):
            return Gst.PadProbeReturn.OK
        ok_par, par_n, par_d = structure.get_fraction('pixel-aspect-ratio')
        if not ok_par:
            par_n = par_d = 1
        width, height = compute_fit_dims(
            src_w, src_h, par_n, par_d, args.fb_width, args.fb_height
        )
        caps_str = build_fit_caps_string(args.fb_format, width, height)
        logging.info(
            'source %dx%d par %d/%d -> fit %s',
            src_w,
            src_h,
            par_n,
            par_d,
            caps_str,
        )
        fit_caps.set_property('caps', Gst.Caps.from_string(caps_str))
        return Gst.PadProbeReturn.OK

    convert.get_static_pad('sink').add_probe(
        Gst.PadProbeType.EVENT_DOWNSTREAM, on_convert_event
    )

    playbin.set_property('uri', args.uri)
    playbin.set_property('video-sink', video_sink)

    if args.audio_device:
        audio_sink = Gst.ElementFactory.make('alsasink')
        if audio_sink is not None:
            audio_sink.set_property('device', args.audio_device)
            playbin.set_property('audio-sink', audio_sink)

    def on_about_to_finish(element: Any) -> None:
        # Gapless loop: re-queue the same URI while the tail of the
        # current iteration is still draining. Runs on a streaming
        # thread — property set only, no state changes here.
        element.set_property('uri', args.uri)

    playbin.connect('about-to-finish', on_about_to_finish)

    loop = GLib.MainLoop()
    exit_code = 0

    def on_bus_message(bus: Any, message: Any) -> bool:
        nonlocal exit_code
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logging.error('pipeline error: %s (%s)', err, debug)
            exit_code = 1
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            # about-to-finish normally pre-queues the next loop and
            # EOS never fires. Some sources can't pre-queue; fall back
            # to a flushing seek, then to a full restart for the
            # non-seekable remainder.
            logging.info('EOS — looping via flush seek')
            if not playbin.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                0,
            ):
                logging.info('seek refused — restarting pipeline')
                playbin.set_state(Gst.State.NULL)
                playbin.set_state(Gst.State.PLAYING)
        return True

    bus = playbin.get_bus()
    bus.add_signal_watch()
    bus.connect('message', on_bus_message)

    def on_sigterm(signum: int, frame: Any) -> None:
        loop.quit()

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)

    if playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        logging.error('could not start playback for %s', args.uri)
        playbin.set_state(Gst.State.NULL)
        return 1

    try:
        loop.run()
    finally:
        playbin.set_state(Gst.State.NULL)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
