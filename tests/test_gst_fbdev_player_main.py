"""Tests for ``gst_fbdev_player.main()`` — the GStreamer/GLib entry point.

``main()`` imports ``gi``/``Gst``/``GLib`` lazily (so the module stays
importable on a dev host without PyGObject), builds a ``playbin``
pipeline, and runs a ``GLib.MainLoop``. These tests stub the gi stack in
``sys.modules`` and drive the pipeline through mocks: the nested
callbacks (bus message, CAPS pad probe, about-to-finish, SIGTERM) are
captured from the mock ``.connect()`` / ``.add_probe()`` /
``signal.signal()`` calls and invoked with fake Gst messages/events, so
the loop's error/EOS/rebuild logic is exercised without a real
GStreamer runtime.
"""

import logging
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import anthias_viewer.gst_fbdev_player as mod

logging.disable(logging.CRITICAL)

ARGV = [
    '--uri',
    'file:///test/video.mp4',
    '--fb-width',
    '1920',
    '--fb-height',
    '1080',
    '--fb-format',
    'RGB16',
    '--rotation',
    '0',
]
AUDIO = ['--audio-device', 'sysdefault:CARD=vc4hdmi']


class _GLibError(Exception):
    """Stand-in for ``GLib.Error`` so ``except GLib.Error`` binds."""


def _make_gst() -> MagicMock:
    Gst = MagicMock(name='Gst')
    # Enum-ish sentinels; strings compare cleanly across the module.
    Gst.State.PLAYING = 'PLAYING'
    Gst.State.NULL = 'NULL'
    Gst.State.READY = 'READY'
    Gst.StateChangeReturn.FAILURE = 'FAILURE'
    Gst.MessageType.ERROR = 'ERROR'
    Gst.MessageType.EOS = 'EOS'
    Gst.EventType.CAPS = 'CAPS'
    Gst.PadProbeReturn.OK = 'PROBE_OK'
    Gst.PadProbeType.EVENT_DOWNSTREAM = 'EVENT_DOWNSTREAM'
    Gst.Format.TIME = 'TIME'
    Gst.SeekFlags.FLUSH = 1
    Gst.SeekFlags.KEY_UNIT = 2
    return Gst


def _harness() -> SimpleNamespace:
    """Build the gi/Gst/GLib mock context (before running ``main``)."""
    Gst = _make_gst()
    GLib = MagicMock(name='GLib')
    GLib.Error = _GLibError
    loop = MagicMock(name='loop')
    GLib.MainLoop.return_value = loop

    playbin = MagicMock(name='playbin')
    # ``flags`` must be int()-able for the no-audio bit-clear path.
    playbin.get_property.side_effect = lambda n: (
        0x7 if n == 'flags' else MagicMock()
    )
    bus = MagicMock(name='bus')
    playbin.get_bus.return_value = bus
    alsasink = MagicMock(name='alsasink')
    video_sink = MagicMock(name='video_sink')
    pad = MagicMock(name='pad')
    video_sink.get_by_name.return_value.get_static_pad.return_value = pad

    def default_make(name: str) -> Any:
        return {'playbin': playbin, 'alsasink': alsasink}.get(
            name, MagicMock()
        )

    Gst.ElementFactory.make.side_effect = default_make
    Gst.parse_bin_from_description.side_effect = lambda desc, ghost: video_sink

    return SimpleNamespace(
        Gst=Gst,
        GLib=GLib,
        loop=loop,
        playbin=playbin,
        bus=bus,
        alsasink=alsasink,
        video_sink=video_sink,
        pad=pad,
        sigterm=[],
    )


def _connected(mock: MagicMock, event: str) -> Any:
    """Return the handler ``mock.connect(event, handler)`` was given."""
    for call in mock.connect.call_args_list:
        if call.args and call.args[0] == event:
            return call.args[1]
    raise AssertionError(f'no handler connected for {event!r}')


def _probe(pad: MagicMock) -> Any:
    """Return the callback ``pad.add_probe(type, cb)`` was given."""
    return pad.add_probe.call_args.args[1]


def _run(ctx: SimpleNamespace, argv: list[str], driver: Any = None) -> int:
    """Run ``main(argv)`` against the harness. ``driver(ctx)`` runs
    inside ``loop.run()`` — that's where the captured callbacks get
    invoked, since they're wired during pipeline build (before run)."""
    if driver is not None:
        ctx.loop.run.side_effect = lambda: driver(ctx)

    gi = MagicMock()
    repo = MagicMock()
    repo.GLib = ctx.GLib
    repo.Gst = ctx.Gst
    with (
        patch.dict(sys.modules, {'gi': gi, 'gi.repository': repo}),
        patch.object(mod, 'clear_framebuffer', return_value=True),
        patch('signal.signal', side_effect=lambda s, h: ctx.sigterm.append(h)),
    ):
        return mod.main(argv)


# --------------------------------------------------------------------------
# Startup / teardown
# --------------------------------------------------------------------------


def test_main_returns_1_when_gi_unavailable() -> None:
    # ``import gi`` raising is the pi-image-regression guard.
    with patch.dict(sys.modules, {'gi': None}):
        assert mod.main(ARGV) == 1


def test_main_happy_path_no_audio_returns_0() -> None:
    ctx = _harness()
    rc = _run(ctx, ARGV)  # no --audio-device → video only
    assert rc == 0
    ctx.playbin.set_property.assert_any_call('uri', 'file:///test/video.mp4')
    ctx.playbin.set_state.assert_any_call('PLAYING')
    # audio bit (0x2) cleared from flags on the no-audio branch.
    ctx.playbin.set_property.assert_any_call('flags', 0x7 & ~0x2)
    # loop teardown ran.
    ctx.playbin.set_state.assert_any_call('NULL')


def test_main_with_audio_wires_alsasink() -> None:
    ctx = _harness()
    rc = _run(ctx, ARGV + AUDIO)
    assert rc == 0
    ctx.alsasink.set_property.assert_any_call(
        'device', 'sysdefault:CARD=vc4hdmi'
    )


def test_unusable_audio_device_degrades_to_video_only() -> None:
    ctx = _harness()
    # Pre-flight READY state fails → audio_device_usable() is False.
    ctx.alsasink.set_state.return_value = 'FAILURE'
    rc = _run(ctx, ARGV + AUDIO)
    assert rc == 0
    # No audio-sink wired: flags path taken instead.
    ctx.playbin.set_property.assert_any_call('flags', 0x7 & ~0x2)


# --------------------------------------------------------------------------
# build_and_start failure paths
# --------------------------------------------------------------------------


def test_playbin_unavailable_returns_1() -> None:
    ctx = _harness()
    ctx.Gst.ElementFactory.make.side_effect = lambda name: (
        None if name == 'playbin' else MagicMock()
    )
    assert _run(ctx, ARGV) == 1


def test_sink_build_error_returns_1() -> None:
    ctx = _harness()
    ctx.Gst.parse_bin_from_description.side_effect = _GLibError('bad caps')
    assert _run(ctx, ARGV) == 1


def test_playing_state_failure_returns_1() -> None:
    ctx = _harness()
    ctx.playbin.set_state.return_value = 'FAILURE'  # PLAYING fails
    assert _run(ctx, ARGV) == 1
    # Failed pipeline is torn down.
    ctx.playbin.set_state.assert_any_call('NULL')


# --------------------------------------------------------------------------
# Bus message handling
# --------------------------------------------------------------------------


def _error_msg(ctx: SimpleNamespace) -> MagicMock:
    msg = MagicMock()
    msg.src = MagicMock()  # not None
    msg.type = ctx.Gst.MessageType.ERROR
    msg.parse_error.return_value = ('boom', 'debug-detail')
    return msg


def test_bus_error_without_audio_exits_1() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        _connected(ctx.bus, 'message')(ctx.bus, _error_msg(ctx))

    assert _run(ctx, ARGV, driver) == 1
    ctx.loop.quit.assert_called()


def test_bus_error_with_audio_rebuilds_without_audio() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        _connected(ctx.bus, 'message')(ctx.bus, _error_msg(ctx))

    rc = _run(ctx, ARGV + AUDIO, driver)
    assert rc == 0  # rebuilt video-only, no quit
    # playbin built twice: once with audio, once on rebuild.
    assert ctx.playbin.set_state.call_args_list.count((('PLAYING',), {})) >= 2


def test_bus_error_with_audio_exits_when_rebuild_also_fails() -> None:
    ctx = _harness()
    # First (audio) build succeeds; the video-only rebuild fails.
    ctx.Gst.parse_bin_from_description.side_effect = [
        ctx.video_sink,
        _GLibError('rebuild failed'),
    ]

    def driver(ctx: SimpleNamespace) -> None:
        _connected(ctx.bus, 'message')(ctx.bus, _error_msg(ctx))

    assert _run(ctx, ARGV + AUDIO, driver) == 1
    ctx.loop.quit.assert_called()


def test_bus_message_ignored_when_no_playbin() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        msg = _error_msg(ctx)
        msg.src = None  # src None → early return, no exit
        assert _connected(ctx.bus, 'message')(ctx.bus, msg) is True

    assert _run(ctx, ARGV, driver) == 0


def test_bus_eos_loops_via_flush_seek() -> None:
    ctx = _harness()
    ctx.playbin.seek_simple.return_value = True

    def driver(ctx: SimpleNamespace) -> None:
        msg = MagicMock(src=MagicMock(), type=ctx.Gst.MessageType.EOS)
        _connected(ctx.bus, 'message')(ctx.bus, msg)

    assert _run(ctx, ARGV, driver) == 0
    ctx.playbin.seek_simple.assert_called_once()


def test_bus_eos_restarts_when_seek_refused() -> None:
    ctx = _harness()
    ctx.playbin.seek_simple.return_value = False

    def driver(ctx: SimpleNamespace) -> None:
        msg = MagicMock(src=MagicMock(), type=ctx.Gst.MessageType.EOS)
        _connected(ctx.bus, 'message')(ctx.bus, msg)

    assert _run(ctx, ARGV, driver) == 0
    # Full NULL→PLAYING restart fallback.
    ctx.playbin.set_state.assert_any_call('NULL')


# --------------------------------------------------------------------------
# CAPS probe / about-to-finish / SIGTERM
# --------------------------------------------------------------------------


def test_caps_probe_pins_fit_caps() -> None:
    ctx = _harness()
    fit_caps = MagicMock(name='fit_caps')
    parent = ctx.pad.get_parent_element.return_value.get_parent.return_value
    parent.get_by_name.return_value = fit_caps

    def driver(ctx: SimpleNamespace) -> None:
        structure = MagicMock()
        structure.get_int.side_effect = lambda k: (True, 1080)
        structure.get_fraction.return_value = (True, 1, 1)
        event = MagicMock(type=ctx.Gst.EventType.CAPS)
        event.parse_caps.return_value.get_structure.return_value = structure
        info = MagicMock()
        info.get_event.return_value = event
        result = _probe(ctx.pad)(ctx.pad, info)
        assert result == ctx.Gst.PadProbeReturn.OK

    _run(ctx, ARGV, driver)
    fit_caps.set_property.assert_called_once()


def test_caps_probe_ignores_non_caps_event() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        event = MagicMock(type='SEGMENT')  # not CAPS
        info = MagicMock()
        info.get_event.return_value = event
        assert _probe(ctx.pad)(ctx.pad, info) == ctx.Gst.PadProbeReturn.OK

    _run(ctx, ARGV, driver)


def _caps_probe_info(ctx: SimpleNamespace, structure: MagicMock) -> MagicMock:
    event = MagicMock(type=ctx.Gst.EventType.CAPS)
    event.parse_caps.return_value.get_structure.return_value = structure
    info = MagicMock()
    info.get_event.return_value = event
    return info


def test_caps_probe_bails_on_incomplete_dimensions() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        structure = MagicMock()
        structure.get_int.side_effect = lambda k: (False, 0)  # width unknown
        info = _caps_probe_info(ctx, structure)
        assert _probe(ctx.pad)(ctx.pad, info) == ctx.Gst.PadProbeReturn.OK

    _run(ctx, ARGV, driver)
    ctx.pad.get_parent_element.assert_not_called()


def test_caps_probe_defaults_par_when_absent() -> None:
    ctx = _harness()
    fit_caps = MagicMock(name='fit_caps')
    parent = ctx.pad.get_parent_element.return_value.get_parent.return_value
    parent.get_by_name.return_value = fit_caps

    def driver(ctx: SimpleNamespace) -> None:
        structure = MagicMock()
        structure.get_int.side_effect = lambda k: (True, 1080)
        structure.get_fraction.return_value = (False, 0, 0)  # PAR unknown
        info = _caps_probe_info(ctx, structure)
        _probe(ctx.pad)(ctx.pad, info)

    _run(ctx, ARGV, driver)
    fit_caps.set_property.assert_called_once()


def test_audio_pre_flight_false_when_alsasink_missing() -> None:
    ctx = _harness()
    # No alsasink factory → audio_device_usable() returns False early.
    ctx.Gst.ElementFactory.make.side_effect = lambda name: (
        ctx.playbin if name == 'playbin' else None
    )
    rc = _run(ctx, ARGV + AUDIO)
    assert rc == 0
    ctx.playbin.set_property.assert_any_call('flags', 0x7 & ~0x2)


def test_about_to_finish_requeues_same_uri() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        element = MagicMock()
        _connected(ctx.playbin, 'about-to-finish')(element)
        element.set_property.assert_called_once_with(
            'uri', 'file:///test/video.mp4'
        )

    _run(ctx, ARGV, driver)


def test_sigterm_quits_loop() -> None:
    ctx = _harness()

    def driver(ctx: SimpleNamespace) -> None:
        ctx.sigterm[0](15, None)  # SIGTERM handler

    _run(ctx, ARGV, driver)
    ctx.loop.quit.assert_called()
