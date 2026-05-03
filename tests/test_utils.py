# coding=utf-8

import io
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
import sh

from anthias_common import utils
from anthias_common.utils import (
    generate_perfect_paper_password,
    handler,
    is_balena_app,
    is_ci,
    is_demo_node,
    is_docker,
    json_dump,
    string_to_bool,
    template_handle_unicode,
    url_fails,
    validate_url,
)


def test_unicode_correctness_in_bottle_templates() -> None:
    assert template_handle_unicode('hello') == 'hello'
    assert template_handle_unicode('Привет') == 'Привет'


def test_json_tz() -> None:
    json_str = handler(datetime(2016, 7, 19, 12, 42))
    assert json_str == '2016-07-19T12:42:00+00:00'


@pytest.mark.django_db
def test_url_fails_returns_true_on_connection_error() -> None:
    with patch(
        'anthias_common.utils.requests.head',
        side_effect=requests.ConnectionError,
    ):
        assert url_fails('http://doesnotwork.example.com') is True


@pytest.mark.django_db
def test_url_fails_returns_false_on_2xx_response() -> None:
    fake = MagicMock()
    fake.ok = True
    with patch('anthias_common.utils.requests.head', return_value=fake):
        assert url_fails('http://example.com') is False


@pytest.mark.django_db
def test_url_fails_short_circuits_for_invalid_url() -> None:
    # validate_url() rejects schemeless paths, so url_fails should
    # return False without ever touching the network layer.
    with patch('anthias_common.utils.requests.head') as mock_head:
        assert url_fails('/home/user/file') is False
    mock_head.assert_not_called()


@pytest.mark.django_db
def test_rtsp_ffprobe_success_returns_false() -> None:
    with patch('anthias_common.utils.sh.Command') as mock_command:
        mock_command.return_value.return_value = ''
        assert not url_fails('rtsp://example.com/stream')
        mock_command.assert_called_once_with('ffprobe')


@pytest.mark.django_db
def test_rtmp_ffprobe_nonzero_exit_returns_true() -> None:
    err = sh.ErrorReturnCode_1('ffprobe', b'', b'cannot open stream')
    with patch('anthias_common.utils.sh.Command') as mock_command:
        mock_command.return_value.side_effect = err
        assert url_fails('rtmp://example.com/live')


@pytest.mark.django_db
def test_rtsp_ffprobe_timeout_returns_true() -> None:
    with patch('anthias_common.utils.sh.Command') as mock_command:
        mock_command.return_value.side_effect = sh.TimeoutException(
            124, 'ffprobe ...'
        )
        assert url_fails('rtsp://example.com/stream')


@pytest.mark.django_db
def test_rtsp_ffprobe_missing_returns_false() -> None:
    with patch('anthias_common.utils.sh.Command') as mock_command:
        mock_command.side_effect = sh.CommandNotFound('ffprobe')
        assert not url_fails('rtsp://example.com/stream')


@pytest.mark.parametrize(
    'value,expected',
    [
        ('y', True),
        ('Yes', True),
        ('t', True),
        ('TRUE', True),
        ('on', True),
        ('1', True),
        ('n', False),
        ('No', False),
        ('f', False),
        ('FALSE', False),
        ('off', False),
        ('0', False),
        (1, True),
        (0, False),
        (True, True),
        (False, False),
    ],
)
def test_string_to_bool_valid(value: Any, expected: bool) -> None:
    assert string_to_bool(value) is expected


@pytest.mark.parametrize('value', ['maybe', 'foo', '', '2'])
def test_string_to_bool_invalid_raises(value: str) -> None:
    with pytest.raises(ValueError):
        string_to_bool(value)


@pytest.mark.parametrize(
    'url,expected',
    [
        ('http://wireload.net/logo.png', True),
        ('https://wireload.net/logo.png', True),
        ('rtsp://example.com/stream', True),
        ('rtmp://example.com/stream', True),
        ('hello', False),
        ('ftp://example.com', False),
        ('http://', False),
        ('', False),
    ],
)
def test_validate_url(url: str, expected: bool) -> None:
    assert validate_url(url) is expected


def test_is_ci_true(monkeypatch: Any) -> None:
    monkeypatch.setenv('CI', 'true')
    assert is_ci() is True


def test_is_ci_false(monkeypatch: Any) -> None:
    monkeypatch.delenv('CI', raising=False)
    assert is_ci() is False


def test_is_balena_app_true(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA', '1')
    assert is_balena_app() is True


def test_is_balena_app_false(monkeypatch: Any) -> None:
    monkeypatch.delenv('BALENA', raising=False)
    assert is_balena_app() is False


def test_is_demo_node_true(monkeypatch: Any) -> None:
    monkeypatch.setenv('IS_DEMO_NODE', '1')
    assert is_demo_node() is True


def test_is_demo_node_false(monkeypatch: Any) -> None:
    monkeypatch.delenv('IS_DEMO_NODE', raising=False)
    assert is_demo_node() is False


def test_is_docker_uses_dockerenv_marker() -> None:
    with patch('anthias_common.utils.os.path.isfile', return_value=True):
        assert is_docker() is True
    with patch('anthias_common.utils.os.path.isfile', return_value=False):
        assert is_docker() is False


def test_generate_perfect_paper_password_length() -> None:
    pw = generate_perfect_paper_password(pw_length=12)
    assert len(pw) == 12


def test_generate_perfect_paper_password_no_symbols_excludes_punctuation() -> (
    None
):
    pw = generate_perfect_paper_password(pw_length=200, has_symbols=False)
    # !#%+ etc. removed when has_symbols=False.
    for ch in '!#%+:?@=':
        assert ch not in pw, f'Symbol {ch!r} should not appear'


def test_json_dump_serialises_datetime() -> None:
    out = json_dump({'when': datetime(2026, 1, 1, 12, 0, 0)})
    assert '"2026-01-01T12:00:00+00:00"' in out


def test_handler_raises_for_non_serializable() -> None:
    with pytest.raises(TypeError):
        handler(object())


def test_get_balena_supervisor_api_response_uses_env(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://supervisor:5000')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    with patch(
        'anthias_common.utils.requests.get', return_value=fake
    ) as mock_get:
        result = utils.get_balena_supervisor_api_response('get', 'device')
    assert result is fake
    url = mock_get.call_args.args[0]
    assert 'http://supervisor:5000/v1/device?apikey=k' == url


def test_get_balena_device_info_calls_v1_device(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://x')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    with patch(
        'anthias_common.utils.requests.get', return_value=fake
    ) as mock_get:
        utils.get_balena_device_info()
    assert '/v1/device' in mock_get.call_args.args[0]


def test_reboot_via_balena_supervisor_uses_post(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://x')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    with patch(
        'anthias_common.utils.requests.post', return_value=fake
    ) as mock_post:
        utils.reboot_via_balena_supervisor()
    assert '/v1/reboot' in mock_post.call_args.args[0]


def test_shutdown_via_balena_supervisor_uses_post(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://x')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    with patch(
        'anthias_common.utils.requests.post', return_value=fake
    ) as mock_post:
        utils.shutdown_via_balena_supervisor()
    assert '/v1/shutdown' in mock_post.call_args.args[0]


def test_get_balena_supervisor_version_ok(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://x')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {'version': '14.2.3'}
    with patch('anthias_common.utils.requests.get', return_value=fake):
        assert utils.get_balena_supervisor_version() == '14.2.3'


def test_get_balena_supervisor_version_error(monkeypatch: Any) -> None:
    monkeypatch.setenv('BALENA_SUPERVISOR_ADDRESS', 'http://x')
    monkeypatch.setenv('BALENA_SUPERVISOR_API_KEY', 'k')
    fake = MagicMock()
    fake.ok = False
    with patch('anthias_common.utils.requests.get', return_value=fake):
        assert (
            utils.get_balena_supervisor_version()
            == 'Error getting the Supervisor version'
        )


def test_template_handle_unicode_non_string() -> None:
    assert template_handle_unicode(42) == '42'
    assert template_handle_unicode(None) == 'None'


# ---------------------------------------------------------------------------
# Resolution detection — the helpers detect_screen_resolution() chains
# through. Each is pure I/O so we mock /sys readers with monkeypatch.


def test_drm_resolution_picks_first_connected_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /sys/class/drm/card?-HDMI-A-1 dir reads as 'connected' with a
    1920x1080 mode → _drm_resolution() returns '1920x1080'."""
    from anthias_common import utils

    class FakeEntry:
        def __init__(self, name: str, path: str) -> None:
            self.name = name
            self.path = path

    monkeypatch.setattr(
        'anthias_common.utils.os.scandir',
        lambda _p: [FakeEntry('card1-HDMI-A-1', '/fake/drm/card1-HDMI-A-1')],
    )

    def fake_open(path: str, *_a: Any, **_k: Any) -> io.StringIO:
        if path.endswith('/status'):
            return io.StringIO('connected\n')
        if path.endswith('/modes'):
            return io.StringIO('1920x1080\n1280x720\n')
        raise OSError('unexpected path')

    monkeypatch.setattr('builtins.open', fake_open)
    assert utils._drm_resolution() == '1920x1080'


def test_fb_resolution_parses_comma_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    monkeypatch.setattr(
        'builtins.open', lambda *_a, **_k: io.StringIO('1920,1080\n')
    )
    assert utils._fb_resolution() == '1920x1080'


def test_fb_resolution_handles_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError('no fb0')

    monkeypatch.setattr('builtins.open', boom)
    assert utils._fb_resolution() is None


# ---------------------------------------------------------------------------
# MAC interface detection — _detect_local_mac() picks default-route
# iface from /proc/net/route then reads /sys/class/net/<iface>/address.


def test_default_route_iface_picks_up_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    sample = (
        'Iface\tDestination\tGateway\tFlags\n'
        'eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\n'
        'eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\n'
    )
    monkeypatch.setattr('builtins.open', lambda *_a, **_k: io.StringIO(sample))
    assert utils._default_route_iface() == 'eth0'


def test_default_route_iface_skips_down_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    # Same destination but RTF_UP=0 (flags=0002) — should NOT match.
    sample = (
        'Iface\tDestination\tGateway\tFlags\n'
        'eth0\t00000000\t0100A8C0\t0002\t0\t0\t100\n'
    )
    monkeypatch.setattr('builtins.open', lambda *_a, **_k: io.StringIO(sample))
    assert utils._default_route_iface() is None


def test_read_iface_mac_skips_zero_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    monkeypatch.setattr(
        'builtins.open', lambda *_a, **_k: io.StringIO('00:00:00:00:00:00\n')
    )
    assert utils._read_iface_mac('eth0') is None


def test_read_iface_mac_returns_real_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    monkeypatch.setattr(
        'builtins.open', lambda *_a, **_k: io.StringIO('aa:bb:cc:dd:ee:ff\n')
    )
    assert utils._read_iface_mac('eth0') == 'aa:bb:cc:dd:ee:ff'


def test_first_non_loopback_mac_skips_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    monkeypatch.setattr(
        'anthias_common.utils.os.listdir',
        lambda _p: ['lo', 'docker0', 'eth0', 'br-foo'],
    )
    monkeypatch.setattr(
        utils,
        '_read_iface_mac',
        lambda iface: 'aa:bb:cc:dd:ee:ff' if iface == 'eth0' else None,
    )
    assert utils._first_non_loopback_mac() == 'aa:bb:cc:dd:ee:ff'


def test_detect_local_mac_prefers_default_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthias_common import utils

    monkeypatch.setattr('anthias_common.utils.os.path.isdir', lambda _p: True)
    monkeypatch.setattr(utils, '_default_route_iface', lambda: 'wlan0')
    monkeypatch.setattr(
        utils,
        '_read_iface_mac',
        lambda iface: '11:22:33:44:55:66' if iface == 'wlan0' else None,
    )
    assert utils._detect_local_mac() == '11:22:33:44:55:66'


# ---------------------------------------------------------------------------
# SSRF guard — url_fails must reject hosts that resolve to private /
# loopback / link-local addresses unless the operator opted in via the
# env var. Test the resolver helper directly so we don't need DNS.


@pytest.mark.parametrize(
    'fake_addr,is_private',
    [
        ('10.0.0.1', True),
        ('192.168.1.50', True),
        ('172.20.5.5', True),
        ('127.0.0.1', True),
        ('169.254.1.1', True),  # link-local
        ('::1', True),  # ipv6 loopback
        ('fe80::1', True),  # ipv6 link-local
        ('8.8.8.8', False),
        ('1.1.1.1', False),
    ],
)
def test_is_private_address_classification(
    fake_addr: str, is_private: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anthias_common import utils

    monkeypatch.delenv('ANTHIAS_ALLOW_PRIVATE_FETCH', raising=False)
    family = (
        (
            utils.__dict__['socket'].AF_INET6
            if ':' in fake_addr
            else utils.__dict__['socket'].AF_INET
        )
        if 'socket' in utils.__dict__
        else 2
    )
    monkeypatch.setattr(
        'anthias_common.utils.socket.getaddrinfo',
        lambda host, port: [(family, 0, 0, '', (fake_addr, 0))],
    )
    assert utils._is_private_address('any.host') is is_private


def test_is_private_address_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthias_common import utils

    monkeypatch.setenv('ANTHIAS_ALLOW_PRIVATE_FETCH', '1')
    # Even a private result returns False — operator opted in.
    monkeypatch.setattr(
        'anthias_common.utils.socket.getaddrinfo',
        lambda host, port: [(2, 0, 0, '', ('10.0.0.1', 0))],
    )
    assert utils._is_private_address('intranet.local') is False


def test_url_fails_rejects_private_http_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A http:// URL whose host resolves to RFC1918 must be marked
    'fails' — the sweep then flags it un-reachable instead of the
    server probing it."""
    from anthias_common import utils

    monkeypatch.delenv('ANTHIAS_ALLOW_PRIVATE_FETCH', raising=False)
    monkeypatch.setattr(
        'anthias_common.utils.socket.getaddrinfo',
        lambda host, port: [(2, 0, 0, '', ('10.0.0.1', 0))],
    )
    # If the SSRF guard fires, requests.head must not be called.
    called: list[Any] = []
    monkeypatch.setattr(
        'anthias_common.utils.requests.head',
        lambda *a, **k: called.append(1),
    )
    assert utils.url_fails('http://intranet.local/admin') is True
    assert called == []
