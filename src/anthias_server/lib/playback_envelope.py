"""Per-board *decode envelope* for video assets.

``processing._HW_DECODE_VIDEO_CODECS`` answers "can this board
hardware-decode this **codec** at all?". It does not answer the second
question, which is what this module adds: given that the codec is
right, is the *stream itself* — its frame size, its pixel format —
inside the envelope that board's decoder can actually take?

The gap is not theoretical, and it is not a prediction. Measured on a
Pi 4B with a 3840x2160 High L5.1 clip at 115 Mbps — the shape of an
export that reached a real screen — against a 1920x1080 re-encode of
the *same content*:

                        4K            1080p
    hardware decode     REFUSED       75 fps
    software, 4 cores   10 fps        37 fps
    software, 1 thread   3 fps        12 fps

The 4K clip does not decode slowly in hardware; it never gets in.
``h264_v4l2m2m`` fails outright with ``Error while opening decoder``
because 3840 is past the device's frame bound, so libavcodec falls
back to software — where 10 fps on an otherwise idle four-core box is
already under the 25 fps the clip needs, before the viewer has spent
anything on presentation. Sharing those cores with QtWebEngine and the
scene graph drags it toward the single-thread figure, which is what
puts a real screen at the ~4 fps operators actually report.

Meanwhile the same content at 1080p hardware-decodes at 3x realtime.
Nothing in the pipeline says a word about any of this. That is the
failure this module exists to name.

Two tiers, because the evidence for each is of a different quality:

``BLOCKING``
    Driver-enforced facts. The decoder *cannot* accept the stream —
    not "will be slow", but "``VIDIOC_S_FMT`` refuses it". There is no
    configuration, no firmware knob and no future kernel that makes
    the format work on that silicon, so refusing the upload costs the
    operator nothing they could have had.

``ADVISORY``
    Judgement calls — currently just software decode that is probably
    too slow. Real problems, but the threshold is a considered guess
    rather than a constant lifted out of a driver, so they annotate
    and never block. Keep the bar high for adding to this tier: a
    bitrate rule lived here briefly and was removed once measurement
    showed it would only have produced false positives.

Every predicate here is written to fail *open*: a dimension we could
not measure (``None``, 0, an unparseable ``pix_fmt``) yields no
warning. A false positive costs an operator a working asset and
teaches them to ignore the badge; a false negative costs them the same
silent 4 fps they get today. Given the choice we take the miss.

Sources for the blocking envelope
---------------------------------

``drivers/staging/vc04_services/bcm2835-codec/bcm2835-v4l2-codec.c``
(raspberrypi/linux) sets the hard frame-size bound for every
VideoCore H.264 decode path::

    #define MIN_W        32
    #define MIN_H        32
    #define MAX_W_CODEC  1920
    #define MAX_H_CODEC  1920

Note that the bound is **1920 on both axes**, not "1080p": a portrait
1080x1920 signage clip is inside the envelope and must not be flagged.
The same table restricts the decoder's capture queue to 8-bit 4:2:0
(``YUV420`` / ``YVU420`` / ``NV12`` / ``NV21`` / ``NV12_COL128``) with
no 10-bit or 4:2:2 YUV entry, which is why High 10 and 4:2:2 sources
fall to software.

The driver deliberately does **not** validate the bitstream's declared
H.264 *level*: ``V4L2_CID_MPEG_VIDEO_H264_LEVEL`` is exposed
``V4L2_CTRL_FLAG_READ_ONLY``. Raspberry Pi's own engineers put the
hardware at "nominally level 4.1, although level 4.2 is generally
achievable", and note it "can decode level 5.0 (and possibly 5.1), but
there is no guarantee that the macroblocks/sec required will be
achieved". So the level field is a *symptom* of an oversized stream,
never the cause, and gating on it would reject the many 1080p files
that carry an inflated level tag and play perfectly. We record the
level for diagnostics and branch on frame size instead.
"""

import os
import time
from typing import Any

from anthias_common.board import resolve_device_key

# Severity tiers. ``BLOCKING`` is reserved for driver-enforced limits
# (see the module docstring); everything softer is ``ADVISORY`` and
# only ever annotates.
BLOCKING = 'blocking'
ADVISORY = 'advisory'


class PlaybackWarning:
    """One reason an asset will not play well on the current board.

    Deliberately a plain object rather than an exception: the same
    finding has to serve two callers with opposite needs. The upload
    gate turns a ``BLOCKING`` warning into a rejection, while the
    asset list renders whatever it finds — blocking or advisory —
    against rows that are already on disk and must keep playing.
    """

    def __init__(
        self,
        code: str,
        severity: str,
        message: str,
        remedy: str = '',
    ) -> None:
        self.code = code
        self.severity = severity
        self.message = message
        self.remedy = remedy

    @property
    def is_blocking(self) -> bool:
        return self.severity == BLOCKING

    def as_dict(self) -> dict[str, str]:
        """Serialise for ``metadata`` / template consumption."""
        return {
            'code': self.code,
            'severity': self.severity,
            'message': self.message,
            'remedy': self.remedy,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlaybackWarning):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        return f'PlaybackWarning({self.code!r}, {self.severity!r})'


# Boards whose H.264 decode path is the VideoCore ``bcm2835-codec``
# V4L2 M2M device at ``/dev/video10``, and which therefore inherit its
# 1920x1920 frame bound and 8-bit 4:2:0 capture queue verbatim.
#
#   pi2 / pi3      GStreamer ``v4l2h264dec`` (GstFbdevMediaPlayer)
#   pi3-64         GStreamer ``v4l2h264dec`` -> kmssink overlay plane
#   pi4-64         QtMultimedia -> libavcodec ``h264_v4l2m2m``
#
# Different players, one decoder. ``pi5`` is absent on purpose: BCM2712
# dropped the H.264 block entirely, so H.264 there is software decode
# on the Cortex-A76 and gets the advisory treatment below instead.
BCM2835_H264_BOARDS = frozenset({'pi2', 'pi3', 'pi3-64', 'pi4-64'})

# ``MAX_W_CODEC`` / ``MAX_H_CODEC`` from bcm2835-v4l2-codec.c. Applied
# per axis, never as a pixel-area budget — 1080x1920 portrait is
# inside the envelope and 3840x2160 is outside it on the width alone.
BCM2835_MAX_DIMENSION = 1920

# Boards on which H.264 is decoded in software. Pi 5's BCM2712 has no
# H.264 hardware block at all (the codec gate accepts H.264 there
# anyway, because a Cortex-A76 clears 1080p comfortably and YouTube
# rarely serves HEVC). Past a point that stops being true, but "an
# A76 will not hold 4K30 in software" is a performance judgement, not
# a driver constant, so it is advisory.
#
# Applied PER AXIS, like the hardware bound, which is an admitted
# approximation: software decode is bound by *pixels per second*, not
# by either axis, so a 1920x1920 clip (3.7 Mpx, nearly twice 1080p)
# draws no advisory today. An area budget would model the cost
# properly. It is not used because nobody has measured where the A76
# actually falls over, and inventing a threshold is the failure this
# module exists to avoid — the same reasoning that removed the
# bitrate rule. Measure before tightening this.
SOFTWARE_H264_BOARDS = frozenset({'pi5'})
SOFTWARE_H264_MAX_DIMENSION = 1920

# Every codec that has a rule below. ``evaluate`` returns early
# for anything else, which is what keeps the asset list off the
# network for images and non-H.264 video. Keep in step with the
# dispatch in ``evaluate``.
_CODECS_WITH_RULES = frozenset({'h264'})

# There is deliberately NO bitrate rule here.
#
# An earlier revision carried one, set at the H.264 High-profile
# Level 4.2 ceiling (62.5 Mbps) on the reasoning that a stream past
# the decoder's nominal level might stutter. Measured on a Pi 4B and
# it is simply not true. Decoding 1080p25 through h264_v4l2m2m, with
# the clip looped to 750 frames:
#
#     ~9 Mbps    93 fps        ~91 Mbps   75 fps
#     ~45 Mbps   83 fps       ~137 Mbps   62 fps
#
# Throughput does fall as the bitrate climbs, but at 137 Mbps — more
# than twice the threshold that rule would have used — hardware decode
# still runs at 2.5x realtime, and the decoder accepted every clip
# without error. The rule would have flagged files that play fine,
# which is the exact failure this module is built to avoid.
#
# Cross-checked against real High-profile content with B-frames rather
# than the synthetic noise those figures used: 1080p at 30 Mbps
# hardware-decodes at 75 fps, in line with the sweep. The fixed-
# function decoder is barely content-sensitive, so the conclusion
# holds. (Software decode is a different story — real content measured
# ~5x more expensive than the synthetic clips, which is exactly why
# nothing here reasons about software throughput from a benchmark.)
#
# ``video_bit_rate`` is still recorded in asset metadata; it is useful
# when diagnosing a report, it is just not evidence of a playback
# problem on its own. If a bitrate rule is ever reintroduced it needs
# a measurement showing real content failing, not a spec table.

# Substrings that positively identify a pixel format the VideoCore
# decoder's capture queue cannot produce. Matched as a denylist rather
# than checking membership of an 8-bit-4:2:0 allowlist *on purpose*:
# an allowlist turns every ffprobe name we failed to anticipate into a
# false rejection, whereas an unrecognised name here simply produces
# no warning. ``10le`` / ``10be`` / ``12le`` / ``12be`` / ``p010``
# cover High 10 and High 12; ``422`` / ``444`` cover the chroma
# sampling the queue has no fourcc for.
UNSUPPORTED_PIX_FMT_MARKERS = (
    '10le',
    '10be',
    '12le',
    '12be',
    'p010',
    '422',
    '444',
)


# ``resolve_device_key()`` is cheap on a Pi (one ``os.environ`` read),
# but on the catch-all ``arm64`` image it reaches for the host_agent's
# published subtype — a fresh Redis client per call, then a
# ``/proc/device-tree/model`` read when Redis has nothing. That is
# fine once per upload. It is not fine on the asset list, which
# renders every row through ``to_json`` several times and re-renders
# the whole table on a 5 s HTMX poll. Measured on ``arm64``, a 40-row
# render went from roughly 11 ms to a quarter of a second, all of it
# this lookup; ``x86`` was unaffected, because ``DEVICE_TYPE`` is a
# plain env read there and never reaches Redis at all.
#
# Keyed on the raw ``DEVICE_TYPE`` so a test (or a board that is
# re-imaged under a running process) flipping the env var still gets
# its own answer rather than a neighbour's.
#
# The TTL is future-proofing, and worth being straight about: it
# guards nothing reachable today. A compose install can publish
# ``host:board_subtype`` seconds after the server starts, so a
# permanent cache would pin the board to the un-upgraded ``arm64``
# key — but ``resolve_device_key`` only ever upgrades ``arm64`` to
# ``rockpi4``, and neither key appears in any rule set, so the
# module answers ``[]`` before and after. The expiry exists so that
# stops being true the moment a rule set gains either key, rather
# than turning into the silent no-op this module fears most.
_DEVICE_KEY_TTL_SECONDS = 30.0
_device_key_cache: dict[str, tuple[float, str]] = {}

# An explicit seam for the clock, so a test can wind it forward
# without reaching through this module's imports to patch the global
# ``time.monotonic`` — which would apply to everything else running
# in the same process for the duration of the test.
_now = time.monotonic


def _current_device_key() -> str:
    """``resolve_device_key()`` memoised for the asset-list render.

    Bounded staleness rather than a permanent cache — see above.
    """
    raw = os.environ.get('DEVICE_TYPE', '').strip().lower()
    now = _now()
    cached = _device_key_cache.get(raw)
    # ``0 <=`` as well as the TTL: a *negative* age means the entry was
    # stamped from a clock this process is no longer reading, which in
    # practice means a test that faked ``time.monotonic`` and left an
    # entry behind. Without the lower bound that entry reads as fresh
    # for as long as the real monotonic clock stays below it — and
    # CLOCK_MONOTONIC on Linux is uptime, so on a runner booted
    # moments ago it never catches up within the session.
    if cached is not None and 0 <= now - cached[0] < _DEVICE_KEY_TTL_SECONDS:
        return cached[1]
    resolved = resolve_device_key()
    _device_key_cache[raw] = (now, resolved)
    return resolved


def _as_positive_int(value: Any) -> int | None:
    """Coerce a metadata field to a positive int, or ``None``.

    Metadata arrives from ffprobe via JSON and from historical asset
    rows written by older releases, so a field may be an int, a
    numeric string, ``None``, or missing entirely. Anything that is
    not confidently a positive number reads as "not measured" and
    suppresses the warning that depends on it.

    ``OverflowError`` is in the net alongside the obvious two: an
    ``inf`` reaches ``int()`` perfectly happily through ``float()``
    and only fails at the conversion, and an integer too large for a
    float fails on the way in. Both escape as an exception rather
    than a ``None`` unless caught, and this runs inside the template
    filter that renders every asset row — so one odd metadata value
    would take out the whole asset list rather than one warning.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def exceeds_dimension(
    width: Any, height: Any, limit: int = BCM2835_MAX_DIMENSION
) -> bool:
    """``True`` when both axes are known and either is above ``limit``.

    Per axis, not by pixel area: the decoder bound is a bound on each
    dimension, so an ultrawide 5760x1080 clip is out of envelope on
    width despite carrying fewer pixels than 4K, and a portrait
    1080x1920 clip is inside it despite being "taller than 1080p".

    A *partially* measured stream returns ``False`` too, which is
    deliberate rather than an oversight: a known 3840 width with an
    unreadable height clears the gate. Blocking an upload is the
    strong action here, and half a measurement is not enough to take
    it — an asset row whose metadata predates the field, or whose
    ffprobe failed, must not be rejected on a number we never read.
    """
    w = _as_positive_int(width)
    h = _as_positive_int(height)
    if w is None or h is None:
        return False
    return w > limit or h > limit


def is_unsupported_pix_fmt(pix_fmt: Any) -> bool:
    """``True`` when ``pix_fmt`` is certainly outside 8-bit 4:2:0.

    See ``UNSUPPORTED_PIX_FMT_MARKERS`` for why this is a denylist.
    A missing or non-string value returns ``False``.
    """
    if not isinstance(pix_fmt, str) or not pix_fmt:
        return False
    token = pix_fmt.strip().lower()
    return any(marker in token for marker in UNSUPPORTED_PIX_FMT_MARKERS)


def _dimensions_label(metadata: dict[str, Any]) -> str:
    """``'3840x2160'`` for messages, or ``'unknown size'``."""
    w = _as_positive_int(metadata.get('video_width'))
    h = _as_positive_int(metadata.get('video_height'))
    if w is None or h is None:
        return 'unknown size'
    return f'{w}x{h}'


def evaluate(
    metadata: dict[str, Any] | None,
    device_key: str | None = None,
) -> list[PlaybackWarning]:
    """Return every envelope warning that applies to ``metadata``.

    ``metadata`` is an asset's stored metadata dict — the same shape
    ``processing._ffprobe_summary`` writes. ``device_key`` defaults to
    the running board via ``_current_device_key()``, which memoises
    ``resolve_device_key()`` for up to ``_DEVICE_KEY_TTL_SECONDS`` so
    the asset list does not pay a Redis round-trip per row; tests
    pass it explicitly. A board is a
    boot-time fact, so the bounded staleness costs nothing an upload
    would notice.

    Ordering is blocking-first so a caller rendering only the most
    serious finding gets the right one. An empty list means "nothing
    we can prove is wrong with this asset on this board" — which is
    not the same as "this will play well", and callers should not
    present it as a guarantee.
    """
    # ``isinstance`` rather than a falsy check: ``Asset.metadata`` is a
    # free-form JSONField, and an admin edit or a restored backup can
    # put a list or a string in it. A truthy non-dict would reach
    # ``.get`` and raise straight out of the filter that renders every
    # asset row — the one failure this module promises it cannot have.
    if not isinstance(metadata, dict) or not metadata:
        return []
    # Codec first: it is a set lookup, while the board can be a Redis
    # round-trip. Every image, web page and non-H.264 video on the
    # asset list settles here without touching the network.
    #
    # Gated on ``_CODECS_WITH_RULES`` rather than a literal so this
    # guard and the dispatch below cannot drift. Adding an HEVC or
    # AV1 rule means adding its codec here too, and forgetting would
    # fail *silently* — the new rule would simply never run, which is
    # the worst outcome this module has.
    codec = str(metadata.get('video_codec') or '').strip().lower()
    if codec not in _CODECS_WITH_RULES:
        return []
    board = device_key if device_key is not None else _current_device_key()
    warnings: list[PlaybackWarning] = []

    if board in BCM2835_H264_BOARDS:
        warnings.extend(_bcm2835_h264_warnings(metadata, board))
    elif board in SOFTWARE_H264_BOARDS:
        warnings.extend(_software_h264_warnings(metadata))

    warnings.sort(key=lambda w: 0 if w.is_blocking else 1)
    return warnings


def _bcm2835_h264_warnings(
    metadata: dict[str, Any], board: str
) -> list[PlaybackWarning]:
    """Blocking findings for the VideoCore H.264 decoder."""
    found: list[PlaybackWarning] = []
    if exceeds_dimension(
        metadata.get('video_width'), metadata.get('video_height')
    ):
        found.append(
            PlaybackWarning(
                code='h264_frame_too_large',
                severity=BLOCKING,
                message=(
                    f'This video is {_dimensions_label(metadata)}. '
                    f'This screen ({board}) plays H.264 video up to '
                    f'{BCM2835_MAX_DIMENSION} pixels on each side, so '
                    'this one would stutter badly instead of playing '
                    'smoothly.'
                ),
                # Generic on purpose. The rejection path replaces this
                # with a sentence built from the same plan the ffmpeg
                # recipe uses, because the right answer differs by
                # board: a Pi 4 keeps the frame and changes codec, a
                # Pi 3 shrinks it. Naming one of those here would
                # contradict the command printed underneath, which is
                # a mistake this pair has already made three times.
                remedy=(
                    'Convert it to a size or format this screen can '
                    'play. The details are below.'
                ),
            )
        )
    pix_fmt = metadata.get('video_pix_fmt')
    if is_unsupported_pix_fmt(pix_fmt):
        found.append(
            PlaybackWarning(
                code='h264_pixel_format_unsupported',
                severity=BLOCKING,
                message=(
                    f'This video uses the {pix_fmt} colour format. '
                    f'This screen ({board}) can only play 8-bit 4:2:0 '
                    'H.264 video, so this one would stutter badly '
                    'instead of playing smoothly.'
                ),
                remedy=('Convert it to 8-bit 4:2:0. The details are below.'),
            )
        )
    return found


def _software_h264_warnings(
    metadata: dict[str, Any],
) -> list[PlaybackWarning]:
    """Advisory finding for boards that decode H.264 in software."""
    if not exceeds_dimension(
        metadata.get('video_width'),
        metadata.get('video_height'),
        SOFTWARE_H264_MAX_DIMENSION,
    ):
        return []
    return [
        PlaybackWarning(
            code='h264_software_decode_oversized',
            severity=ADVISORY,
            message=(
                f'This video is {_dimensions_label(metadata)}. This '
                'screen has no dedicated H.264 hardware, so it plays '
                'H.264 on the processor. That keeps up around 1080p '
                'but is unlikely to at this size.'
            ),
            remedy=(
                'Convert it so neither side is larger than '
                f'{SOFTWARE_H264_MAX_DIMENSION} pixels, or to HEVC, '
                'which this screen plays in hardware.'
            ),
        )
    ]


def frame_bound_for(codec: str, device_key: str | None = None) -> int | None:
    """The per-axis frame bound a *re-encode* has to meet, or ``None``.

    ``exceeds_dimension`` answers "is this stream too big for the
    bcm2835 decoder", with that decoder's bound baked in as the
    default. That is the right question on the envelope path, where a
    blocking warning has already established the board. It is the
    wrong one for the codec-rejection path, which reaches every board
    — including ``x86`` and ``rockpi4``, whose decode paths are
    deliberately uncharacterised here. Asking ``exceeds_dimension``
    there quietly borrows a Raspberry Pi limit and tells an x86
    operator to downscale a 4K file their board may well play.

    So this answers the question the recipe actually needs: given the
    codec we are about to tell them to encode *to*, does this board
    impose a frame bound on it at all? ``None`` means no bound is
    known, and the recipe should not invent one.

    Both tiers answer yes. A board that merely *decodes slowly* still
    needs the box: skip it and the operator re-encodes, re-uploads,
    and lands an asset wearing an advisory chip for the rest of its
    life — the round trip the rejection existed to spare them.
    """
    board = device_key if device_key is not None else _current_device_key()
    if codec.strip().lower() != 'h264':
        # Only the H.264 path is bounded. The Pi 4's HEVC block does
        # 4Kp60, and 10-bit at that, so an HEVC target inherits none
        # of the bcm2835 H.264 limits.
        return None
    if board in BCM2835_H264_BOARDS:
        return BCM2835_MAX_DIMENSION
    if board in SOFTWARE_H264_BOARDS:
        # Softer in kind — nothing refuses the format, the CPU just
        # cannot keep up — but a recipe that ignores it hands the
        # operator a file this board will flag the moment they upload
        # it, which is the same round trip a rejection is supposed to
        # save them.
        return SOFTWARE_H264_MAX_DIMENSION
    return None


def requires_8bit_420(codec: str, device_key: str | None = None) -> bool:
    """Does a re-encode to ``codec`` have to be 8-bit 4:2:0 here?

    The restriction belongs to the VideoCore H.264 capture queue, not
    to the board. The same Pi 4's HEVC node advertises ``Nc30``/
    ``NC30``, so 10-bit HEVC decodes there in hardware — and forcing
    ``-pix_fmt yuv420p`` on a recipe that targets HEVC would strip bit
    depth the operator's file has and the board can play, to satisfy a
    rule for a codec we are not recommending.
    """
    board = device_key if device_key is not None else _current_device_key()
    return codec.strip().lower() == 'h264' and board in BCM2835_H264_BOARDS


def blocking_warning(
    metadata: dict[str, Any] | None,
    device_key: str | None = None,
) -> PlaybackWarning | None:
    """The first blocking warning for ``metadata``, if any.

    Convenience for the upload gate, which rejects on the first
    blocking finding and does not care about advisories.
    """
    for warning in evaluate(metadata, device_key):
        if warning.is_blocking:
            return warning
    return None
