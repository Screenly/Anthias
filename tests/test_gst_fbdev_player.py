"""Unit tests for the pure helpers in anthias_viewer.gst_fbdev_player.

The GStreamer runtime (``gi``) is only imported inside ``main()``, so
everything here runs on a host without PyGObject — only the aspect-fit
math, caps composition, argv parsing, and fb-clear I/O are exercised.
"""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anthias_viewer.gst_fbdev_player import (
    MAX_OUTPUT_FPS,
    build_fit_caps_string,
    build_sink_description,
    clear_framebuffer,
    compute_fit_dims,
    parse_args,
)

logging.disable(logging.CRITICAL)


@pytest.mark.parametrize(
    ('src', 'fb', 'expected'),
    [
        # Same aspect → fullscreen.
        ((1920, 1080), (1920, 1080), (1920, 1080)),
        # Portrait into landscape fb → pillarbox, height pinned. This
        # is the issue #2987 stretch case: 1080x1920 must NOT fill
        # 1920x1080.
        ((1080, 1920), (1920, 1080), (608, 1080)),
        # 4:3 into 16:9 → pillarbox.
        ((1440, 1080), (1920, 1080), (1440, 1080)),
        # Wider than fb aspect → letterbox, width pinned.
        ((2560, 1080), (1920, 1080), (1920, 810)),
        # Upscale of a small source still fits the fb.
        ((640, 360), (1920, 1080), (1920, 1080)),
        # Odd results round down to even for the ISP.
        ((1080, 1920), (3840, 2160), (1214, 2160)),
    ],
)
def test_compute_fit_dims_square_pixels(
    src: tuple[int, int],
    fb: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    assert compute_fit_dims(*src, 1, 1, *fb) == expected


def test_compute_fit_dims_honours_anamorphic_par() -> None:
    # 720x576 with 16/11 PAR (DVD widescreen) displays as ~16:9, so it
    # letterboxes a 1920x1080 fb at full width.
    width, height = compute_fit_dims(720, 576, 16, 11, 1920, 1080)
    assert width == 1920
    assert abs(height - 1056) <= 2


def test_compute_fit_dims_degrades_to_fullscreen_on_bad_input() -> None:
    assert compute_fit_dims(0, 1080, 1, 1, 1920, 1080) == (1920, 1080)
    # Garbage PAR is treated as square rather than crashing playback.
    assert compute_fit_dims(1920, 1080, 0, 1, 1920, 1080) == (1920, 1080)


def test_build_fit_caps_pins_par() -> None:
    # pixel-aspect-ratio=1/1 is the load-bearing part: without it the
    # converter satisfies a forced WxH by stashing the distortion in
    # the PAR, which fbdevsink ignores (the issue #2987 stretch).
    caps = build_fit_caps_string('RGB16', 608, 1080)
    assert caps == (
        'video/x-raw,format=RGB16,width=608,height=1080,pixel-aspect-ratio=1/1'
    )


def test_build_fit_caps_without_dims_only_pins_format_and_par() -> None:
    assert build_fit_caps_string('BGRx') == (
        'video/x-raw,format=BGRx,pixel-aspect-ratio=1/1'
    )


def _args(rotation: int = 0) -> Any:
    return parse_args(
        [
            '--uri',
            'file:///test/video.mp4',
            '--fb-width',
            '1920',
            '--fb-height',
            '1080',
            '--fb-format',
            'RGB16',
            '--rotation',
            str(rotation),
            '--audio-device',
            'sysdefault:CARD=vc4hdmi',
        ]
    )


def test_sink_description_is_hw_pipeline_with_rate_cap() -> None:
    desc = build_sink_description(_args())
    # videorate ahead of the converter so 50/60 fps sources drop to an
    # even cadence before the ISP + framebuffer blit (which sustain
    # ~40 fps at 1080p on a Pi 3) instead of juddering on late frames.
    assert desc.startswith(
        f'videorate drop-only=true max-rate={MAX_OUTPUT_FPS}'
    )
    assert 'v4l2convert name=fit_convert' in desc
    assert 'capsfilter name=fit_caps' in desc
    assert 'fbdevsink device=/dev/fb0' in desc
    # Unrotated panel → no videoflip, pipeline stays fully hardware.
    assert 'videoflip' not in desc


def test_sink_description_rotation_adds_videoflip() -> None:
    desc = build_sink_description(_args(rotation=90))
    assert 'videoflip method=clockwise' in desc
    assert desc.index('videoflip') < desc.index('v4l2convert')


def test_parse_args_rejects_non_cardinal_rotation() -> None:
    with pytest.raises(SystemExit):
        _args(rotation=45)


def test_clear_framebuffer_writes_stride_times_height() -> None:
    written = bytearray()

    def fake_open(path: str, *open_args: Any, **kwargs: Any) -> Any:
        if path.endswith('/stride'):
            data = '3840\n'
        elif path.endswith('/virtual_size'):
            data = '1920,1080\n'
        else:
            handle = MagicMock()
            handle.write = written.extend
            return MagicMock(
                __enter__=lambda s: handle, __exit__=lambda *a: None
            )
        return MagicMock(
            __enter__=lambda s: MagicMock(read=lambda: data),
            __exit__=lambda *a: None,
        )

    with patch('builtins.open', side_effect=fake_open):
        assert clear_framebuffer() is True
    assert len(written) == 3840 * 1080
    assert not any(written)


def test_clear_framebuffer_is_best_effort() -> None:
    with patch('builtins.open', side_effect=OSError('no fb')):
        assert clear_framebuffer() is False
