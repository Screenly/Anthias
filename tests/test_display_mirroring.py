import json
import os
import subprocess
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

import anthias_viewer as viewer
from anthias_common import board
from anthias_server.settings import settings
from anthias_viewer import display_mirroring as dm


def _mode(
    width: int,
    height: int,
    refresh: float = 60.0,
    *,
    preferred: bool = False,
    current: bool = False,
) -> dict[str, Any]:
    return {
        'width': width,
        'height': height,
        'refresh': refresh,
        'preferred': preferred,
        'current': current,
    }


def _head(
    name: str,
    modes: list[dict[str, Any]],
    *,
    x: int = 0,
    y: int = 0,
    scale: float = 1.0,
    enabled: bool = True,
) -> dict[str, Any]:
    head: dict[str, Any] = {'name': name, 'enabled': enabled, 'modes': modes}
    if enabled:
        head.update(
            {
                'position': {'x': x, 'y': y},
                'transform': 'normal',
                'scale': scale,
            }
        )
    return head


def _plan(*heads: dict[str, Any]) -> list[str]:
    return dm.plan_mirror(dm.parse_outputs(json.dumps(list(heads))))


def test_parse_outputs_skips_disabled_and_modeless_heads() -> None:
    raw = json.dumps(
        [
            _head('HDMI-A-1', [_mode(1920, 1080, current=True)], x=1920),
            _head('HDMI-A-2', [_mode(1920, 1080)], enabled=False),
            _head('HDMI-A-3', [_mode(1920, 1080)]),
        ]
    )
    outputs = dm.parse_outputs(raw)
    assert [o.name for o in outputs] == ['HDMI-A-1']
    assert outputs[0].x == 1920
    assert outputs[0].current.size == (1920, 1080)


@pytest.mark.parametrize('raw', ['', 'not json', '{}', '[1, "x"]'])
def test_parse_outputs_tolerates_garbage(raw: str) -> None:
    assert dm.parse_outputs(raw) == []


def test_single_output_needs_nothing() -> None:
    assert _plan(_head('HDMI-A-1', [_mode(1920, 1080, current=True)])) == []


def test_same_size_side_by_side_is_stacked_without_modeset() -> None:
    args = _plan(
        _head('HDMI-A-1', [_mode(1920, 1080, current=True)], x=0),
        _head('HDMI-A-2', [_mode(1920, 1080, current=True)], x=1920),
    )
    # HDMI-A-1 is already at the origin; only HDMI-A-2 moves, and no
    # --mode means no modeset flicker on either screen.
    assert args == ['--output', 'HDMI-A-2', '--pos', '0,0', '--scale', '1']


def test_already_mirrored_layout_is_left_alone() -> None:
    assert (
        _plan(
            _head('HDMI-A-1', [_mode(1920, 1080, current=True)]),
            _head('HDMI-A-2', [_mode(1920, 1080, current=True)]),
        )
        == []
    )


def test_mixed_resolutions_switch_to_largest_common_mode() -> None:
    four_k = [
        _mode(3840, 2160, 60.0, preferred=True, current=True),
        _mode(1920, 1080, 50.0),
        _mode(1920, 1080, 59.94),
        _mode(1280, 720, 60.0),
    ]
    full_hd = [
        _mode(1920, 1080, 60.0, preferred=True, current=True),
        _mode(1280, 720, 60.0),
    ]
    args = _plan(
        _head('HDMI-A-1', four_k),
        _head('HDMI-A-2', full_hd, x=3840),
    )
    # The 4K screen drops to 1080p at its highest 1080p refresh; the
    # 1080p screen keeps its mode and just moves.
    assert args == [
        '--output',
        'HDMI-A-1',
        '--mode',
        '1920x1080@59.940000Hz',
        '--pos',
        '0,0',
        '--scale',
        '1',
        '--output',
        'HDMI-A-2',
        '--pos',
        '0,0',
        '--scale',
        '1',
    ]


def test_preferred_mode_wins_over_higher_refresh() -> None:
    args = _plan(
        _head(
            'HDMI-A-1',
            [
                _mode(2560, 1440, current=True),
                _mode(1920, 1080, 120.0),
                _mode(1920, 1080, 60.0, preferred=True),
            ],
        ),
        _head('HDMI-A-2', [_mode(1920, 1080, current=True)]),
    )
    assert args[:4] == [
        '--output',
        'HDMI-A-1',
        '--mode',
        '1920x1080@60.000000Hz',
    ]


def test_no_common_mode_falls_back_to_scaling() -> None:
    # Virtual (headless) outputs only expose their current mode.
    args = _plan(
        _head('HEADLESS-1', [_mode(1920, 1080, 0, current=True)], x=3840),
        _head('HEADLESS-2', [_mode(3840, 2160, 0, current=True)]),
    )
    assert args == [
        '--output',
        'HEADLESS-1',
        '--pos',
        '0,0',
        '--scale',
        '1',
        '--output',
        'HEADLESS-2',
        '--pos',
        '0,0',
        '--scale',
        '2',
    ]


def test_mode_without_refresh_omits_rate() -> None:
    assert dm.Mode(1280, 720, 0).wlr_arg() == '1280x720'


def _completed(stdout: str = '', returncode: int = 0) -> Any:
    return subprocess.CompletedProcess([], returncode, stdout, '')


def test_apply_mirror_runs_one_atomic_wlr_randr_call() -> None:
    listing = json.dumps(
        [
            _head('HDMI-A-1', [_mode(1920, 1080, current=True)]),
            _head('HDMI-A-2', [_mode(1920, 1080, current=True)], x=1920),
        ]
    )
    with mock.patch.object(
        subprocess,
        'run',
        side_effect=[_completed(listing), _completed()],
    ) as run:
        assert dm.apply_mirror() is True
    assert run.call_args_list[0].args[0] == ['wlr-randr', '--json']
    assert run.call_args_list[1].args[0] == [
        'wlr-randr',
        '--output',
        'HDMI-A-2',
        '--pos',
        '0,0',
        '--scale',
        '1',
    ]


@pytest.mark.parametrize(
    'side_effect',
    [
        [FileNotFoundError()],
        [subprocess.TimeoutExpired('wlr-randr', 5)],
        [_completed(returncode=1)],
        [_completed('[]')],
    ],
)
def test_apply_mirror_reports_failure_for_retry(side_effect: Any) -> None:
    with mock.patch.object(subprocess, 'run', side_effect=side_effect):
        assert dm.apply_mirror() is False


def test_apply_mirror_reports_rejected_configuration() -> None:
    listing = json.dumps(
        [
            _head('HDMI-A-1', [_mode(1920, 1080, current=True)]),
            _head('HDMI-A-2', [_mode(1920, 1080, current=True)], x=1920),
        ]
    )
    with mock.patch.object(
        subprocess,
        'run',
        side_effect=[_completed(listing), _completed(returncode=1)],
    ):
        assert dm.apply_mirror() is False


@pytest.mark.parametrize(
    ('device_type', 'expected'),
    [
        ('pi5', True),
        ('x86', True),
        ('arm64', True),
        ('pi4-64', False),
        ('pi3', False),
        ('', False),
    ],
)
def test_supports_display_mirroring(device_type: str, expected: bool) -> None:
    with mock.patch.dict(os.environ, {'DEVICE_TYPE': device_type}):
        assert board.supports_display_mirroring() is expected


@pytest.fixture
def mirror_state() -> Iterator[None]:
    prior = (viewer._mirror_applied, viewer._compositor_restart_pending)
    viewer._mirror_applied = False
    viewer._compositor_restart_pending = False
    try:
        yield
    finally:
        viewer._mirror_applied, viewer._compositor_restart_pending = prior


def _wayland_env(cage_mirroring: str) -> Any:
    return mock.patch.dict(
        os.environ,
        {
            'QT_QPA_PLATFORM': 'wayland',
            dm.CAGE_MIRRORING_ENV: cage_mirroring,
        },
    )


@pytest.mark.usefixtures('mirror_state')
def test_ensure_mirroring_latches_after_success() -> None:
    with (
        _wayland_env('1'),
        mock.patch.object(
            dm, 'apply_mirror', side_effect=[False, True]
        ) as apply,
    ):
        viewer._ensure_display_mirroring()
        assert viewer._mirror_applied is False
        viewer._ensure_display_mirroring()
        assert viewer._mirror_applied is True
        viewer._ensure_display_mirroring()
    assert apply.call_count == 2


@pytest.mark.usefixtures('mirror_state')
@pytest.mark.parametrize(
    'env',
    [
        {'QT_QPA_PLATFORM': 'wayland', dm.CAGE_MIRRORING_ENV: '0'},
        {'QT_QPA_PLATFORM': 'eglfs', dm.CAGE_MIRRORING_ENV: '1'},
    ],
)
def test_ensure_mirroring_noop_when_not_mirroring(env: dict[str, str]) -> None:
    with (
        mock.patch.dict(os.environ, env),
        mock.patch.object(dm, 'apply_mirror') as apply,
    ):
        viewer._ensure_display_mirroring()
    apply.assert_not_called()


@pytest.mark.usefixtures('mirror_state')
@pytest.mark.parametrize(
    ('cage', 'setting', 'restart'),
    [
        ('0', True, True),
        ('1', False, True),
        ('1', True, False),
        ('0', False, False),
    ],
)
def test_reload_restarts_compositor_only_on_toggle(
    cage: str, setting: bool, restart: bool
) -> None:
    with (
        _wayland_env(cage),
        mock.patch.dict(settings, {'display_mirroring': setting}),
    ):
        viewer._maybe_restart_for_mirroring()
        assert viewer._compositor_restart_pending is restart
        if restart:
            with pytest.raises(SystemExit) as exc:
                viewer._consume_pending_compositor_restart()
            assert exc.value.code == 0
        else:
            viewer._consume_pending_compositor_restart()


@pytest.mark.usefixtures('mirror_state')
def test_reload_never_restarts_non_wayland_boards() -> None:
    with (
        mock.patch.dict(os.environ, {'QT_QPA_PLATFORM': 'eglfs'}),
        mock.patch.dict(settings, {'display_mirroring': True}),
    ):
        viewer._maybe_restart_for_mirroring()
    assert viewer._compositor_restart_pending is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('device_type', 'shown'), [('pi5', True), ('x86', True), ('pi4-64', False)]
)
def test_settings_page_offers_mirroring_only_on_cage_boards(
    device_type: str, shown: bool
) -> None:
    from django.test import Client
    from django.urls import reverse

    with (
        mock.patch.dict(os.environ, {'DEVICE_TYPE': device_type}),
        mock.patch.dict(settings, {'auth_backend': ''}),
    ):
        html = Client().get(reverse('anthias_app:settings')).content
    assert (b'id="display_mirroring"' in html) is shown
    # The save-time confirmation ships with the toggle and never without.
    assert (b'id="btn-mirroring-confirm"' in html) is shown


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('device_type', 'posted', 'expected'),
    [('pi5', 'true', True), ('pi4-64', 'true', False)],
)
def test_settings_save_ignores_mirroring_on_other_boards(
    device_type: str, posted: str, expected: bool
) -> None:
    from django.test import Client
    from django.urls import reverse

    with (
        mock.patch.dict(os.environ, {'DEVICE_TYPE': device_type}),
        mock.patch.dict(
            settings, {'auth_backend': '', 'display_mirroring': False}
        ),
        mock.patch.object(settings, 'save'),
        mock.patch('anthias_server.app.views.ViewerPublisher.get_instance'),
    ):
        Client().post(
            reverse('anthias_app:settings_save'),
            {
                'player_name': 'Lobby',
                'default_duration': '10',
                'default_streaming_duration': '300',
                'audio_output': 'hdmi',
                'date_format': 'mm/dd/yyyy',
                'auth_backend': '',
                'screen_rotation': '0',
                'display_mirroring': posted,
            },
        )
        assert settings['display_mirroring'] is expected
