"""Tests for the per-board decode envelope.

The negative cases carry as much weight as the positive ones here. A
false positive means an operator is told a working asset is broken,
which trains them to ignore the badge that is supposed to save them
from the 4 fps slideshow — so every "must NOT warn" test below is
guarding a real regression, not padding coverage.
"""

import pytest

from anthias_server.lib import playback_envelope as env


def _meta(**overrides: object) -> dict[str, object]:
    """A metadata dict shaped like ``_ffprobe_summary``'s output."""
    base: dict[str, object] = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 25.0,
        'video_pix_fmt': 'yuv420p',
        'audio_codec': 'aac',
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The reported failure: 4K H.264 on a Pi 4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('board', sorted(env.BCM2835_H264_BOARDS))
def test_4k_h264_blocks_on_every_videocore_board(board: str) -> None:
    """3840x2160 exceeds MAX_W_CODEC on every board that decodes
    H.264 through bcm2835-codec, so all four must block it."""
    warnings = env.evaluate(
        _meta(video_width=3840, video_height=2160), device_key=board
    )
    codes = [w.code for w in warnings]
    assert 'h264_frame_too_large' in codes
    blocking = env.blocking_warning(
        _meta(video_width=3840, video_height=2160), device_key=board
    )
    assert blocking is not None
    assert blocking.is_blocking
    assert '3840x2160' in blocking.message
    assert blocking.remedy


def test_blocking_message_names_the_board_and_the_limit() -> None:
    warning = env.blocking_warning(
        _meta(video_width=3840, video_height=2160), device_key='pi4-64'
    )
    assert warning is not None
    assert 'pi4-64' in warning.message
    assert '1920' in warning.message


# ---------------------------------------------------------------------------
# False positives — the cases that must stay silent
# ---------------------------------------------------------------------------


def test_1080p_tagged_level_51_is_not_flagged() -> None:
    """The level field is a symptom, never the trigger.

    A 1920x1080 stream carrying an inflated ``level=51`` tag decodes
    in hardware on a Pi 4 — the driver exposes the level control
    read-only and never validates the bitstream against it. Flagging
    this was the tempting-but-wrong rule.
    """
    warnings = env.evaluate(
        _meta(video_level=51, video_profile='High'), device_key='pi4-64'
    )
    assert warnings == []


def test_portrait_1080x1920_is_inside_the_envelope() -> None:
    """MAX_H_CODEC is 1920, not 1080.

    Portrait signage is the common case for a lobby screen; treating
    the bound as a 1080p area budget would reject all of it.
    """
    warnings = env.evaluate(
        _meta(video_width=1080, video_height=1920), device_key='pi4-64'
    )
    assert warnings == []


def test_exactly_1920x1920_is_inside_the_envelope() -> None:
    """The bound is inclusive — ``> limit``, not ``>=``."""
    warnings = env.evaluate(
        _meta(video_width=1920, video_height=1920), device_key='pi4-64'
    )
    assert warnings == []


def test_ultrawide_is_flagged_on_width_despite_low_pixel_count() -> None:
    """5760x1080 is fewer pixels than 4K but still out of envelope,
    which is why the check is per-axis rather than by area."""
    warnings = env.evaluate(
        _meta(video_width=5760, video_height=1080), device_key='pi4-64'
    )
    assert [w.code for w in warnings] == ['h264_frame_too_large']


def test_unknown_dimensions_produce_no_warning() -> None:
    """Historical rows predate the width/height metadata fields. An
    asset must never be flagged on a measurement we never took."""
    for width, height in ((None, None), (0, 0), (3840, None), (None, 2160)):
        warnings = env.evaluate(
            _meta(video_width=width, video_height=height),
            device_key='pi4-64',
        )
        assert warnings == [], f'{width}x{height} should not warn'


def test_empty_and_missing_metadata_produce_no_warning() -> None:
    assert env.evaluate(None, device_key='pi4-64') == []
    assert env.evaluate({}, device_key='pi4-64') == []


def test_4k_hevc_on_pi4_is_not_blocked() -> None:
    """The blocking envelope is scoped to the H.264 path only.

    BCM2711 carries a separate 4Kp60 HEVC block, so a 4K HEVC clip
    must not inherit the H.264 decoder's 1920 bound. Confirmed on a
    real Pi 4B: ``/dev/video19`` (``rpi-hevc-dec``) is bound by
    default, with no ``dtoverlay=rpivid-v4l2`` in config.txt.
    """
    warnings = env.evaluate(
        _meta(video_codec='hevc', video_width=3840, video_height=2160),
        device_key='pi4-64',
    )
    assert [w for w in warnings if w.is_blocking] == []


def test_unrecognised_board_produces_no_warning() -> None:
    """x86 and rockpi4 decode paths are not characterised well enough
    to gate on, and an unknown DEVICE_TYPE certainly is not."""
    for board in ('x86', 'rockpi4', '', 'some-future-board'):
        warnings = env.evaluate(
            _meta(video_width=3840, video_height=2160), device_key=board
        )
        assert warnings == [], f'{board!r} should not warn'


# ---------------------------------------------------------------------------
# Pixel format
# ---------------------------------------------------------------------------


def test_10bit_hevc_on_pi4_is_not_blocked() -> None:
    """The 8-bit-4:2:0 restriction is an H.264 fact, not a board fact.

    ``/dev/video19`` on a Pi 4 advertises ``Nc30``/``NC30`` (10-bit
    4:2:0) capture formats, so 10-bit HEVC decodes in hardware there.
    Applying the H.264 decoder's pixel-format limit board-wide would
    reject it.
    """
    warnings = env.evaluate(
        _meta(video_codec='hevc', video_pix_fmt='yuv420p10le'),
        device_key='pi4-64',
    )
    assert warnings == []


@pytest.mark.parametrize(
    'pix_fmt',
    ['yuv420p10le', 'yuv422p', 'yuv444p', 'p010le', 'yuv420p12le'],
)
def test_non_8bit_420_pixel_formats_block(pix_fmt: str) -> None:
    warnings = env.evaluate(_meta(video_pix_fmt=pix_fmt), device_key='pi4-64')
    assert [w.code for w in warnings] == ['h264_pixel_format_unsupported']


@pytest.mark.parametrize('pix_fmt', ['yuv420p', 'yuvj420p', 'nv12', 'nv21'])
def test_8bit_420_pixel_formats_pass(pix_fmt: str) -> None:
    warnings = env.evaluate(_meta(video_pix_fmt=pix_fmt), device_key='pi4-64')
    assert warnings == []


def test_unknown_pixel_format_is_not_flagged() -> None:
    """Denylist, not allowlist — an ffprobe name we did not
    anticipate must produce silence rather than a rejection."""
    for pix_fmt in (None, '', 'some_new_fourcc', 42):
        assert env.is_unsupported_pix_fmt(pix_fmt) is False


# ---------------------------------------------------------------------------
# Advisory tier
# ---------------------------------------------------------------------------


def test_4k_h264_on_pi5_advises_but_does_not_block() -> None:
    """Pi 5 has no H.264 hardware block, so 4K H.264 is software
    decode on the A76 — a real problem, but a performance judgement
    rather than a driver constant, so it must not block."""
    warnings = env.evaluate(
        _meta(video_width=3840, video_height=2160), device_key='pi5'
    )
    assert [w.code for w in warnings] == ['h264_software_decode_oversized']
    assert not warnings[0].is_blocking
    assert (
        env.blocking_warning(
            _meta(video_width=3840, video_height=2160), device_key='pi5'
        )
        is None
    )


def test_1080p_h264_on_pi5_is_silent() -> None:
    assert env.evaluate(_meta(), device_key='pi5') == []


def test_bitrate_alone_never_warns() -> None:
    """There is no bitrate rule, and this pins that.

    Measured on a Pi 4B: 1080p25 H.264 through h264_v4l2m2m decodes at
    93 fps at ~9 Mbps and still 62 fps at ~137 Mbps — 2.5x realtime,
    no decoder errors. A threshold rule would only have flagged files
    that play fine. Do not reintroduce one without a measurement
    showing real content failing.
    """
    for bitrate in (8_000_000, 62_500_000, 115_305_441, 200_000_000):
        warnings = env.evaluate(
            _meta(video_bit_rate=bitrate), device_key='pi4-64'
        )
        assert warnings == [], f'{bitrate} bps must not warn'


def test_multiple_blocking_findings_are_all_reported() -> None:
    """An oversized 10-bit clip fails on both counts; the caller that
    renders only the first still gets a blocking one."""
    warnings = env.evaluate(
        _meta(
            video_width=3840,
            video_height=2160,
            video_pix_fmt='yuv420p10le',
        ),
        device_key='pi4-64',
    )
    assert {w.code for w in warnings} == {
        'h264_frame_too_large',
        'h264_pixel_format_unsupported',
    }
    assert all(w.is_blocking for w in warnings)
    assert warnings[0].is_blocking


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def test_evaluate_defaults_to_the_running_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    warnings = env.evaluate(_meta(video_width=3840, video_height=2160))
    assert [w.code for w in warnings] == ['h264_frame_too_large']


def test_string_dimensions_are_coerced() -> None:
    """ffprobe values round-trip through JSON in stored metadata and
    older rows may carry strings."""
    warnings = env.evaluate(
        _meta(video_width='3840', video_height='2160'), device_key='pi4-64'
    )
    assert [w.code for w in warnings] == ['h264_frame_too_large']


def test_as_dict_round_trips_for_templates() -> None:
    warning = env.blocking_warning(
        _meta(video_width=3840, video_height=2160), device_key='pi4-64'
    )
    assert warning is not None
    payload = warning.as_dict()
    assert set(payload) == {'code', 'severity', 'message', 'remedy'}
    assert payload['severity'] == env.BLOCKING
