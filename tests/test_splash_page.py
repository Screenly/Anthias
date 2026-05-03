"""
Tests for the splash page render path and the IP-list endpoint that
backs its client-side polling.

The splash page used to render IP addresses synchronously by calling
get_node_ip() in the view, which had two failure modes: a slow Balena
supervisor caused get_node_ip() to return 'Unknown', which then
crashed the view with ipaddress.ip_address('Unknown') -> ValueError;
and a static snapshot at render time meant the page couldn't update if
the host bus came back online during the splash's display window.
The view now renders immediately with no IPs and the page polls
/api/v2/network/ip-addresses to populate them.

The polling endpoint must stay fast — get_node_ip() can block up to
~80s on a fresh non-Balena boot waiting for host_agent to populate
Redis, and a single slow first call would tail-back every subsequent
poll. The endpoint reads the Redis cache directly on bare metal and
calls the Balena supervisor with a tight HTTP timeout on Balena.

The IP literals and ``http://`` schemes below are deliberate test
fixtures: arbitrary non-loopback addresses to exercise IPv4/IPv6
formatting, and the ``http://`` scheme matches the splash output
(Anthias is plain HTTP per CLAUDE.md, with TLS as an opt-in Caddy
sidecar). NOSONAR markers suppress S1313 (hardcoded IP) and S5332
(use https) on the centralized constants below; downstream f-strings
and assertions inherit the suppression by referencing them.
"""

import json
from unittest import mock

import pytest
import redis
import requests
from django.test import Client

from anthias_server.api.views import v2 as v2_views

_FIXTURE_IPV4 = '192.168.1.42'  # NOSONAR
_FIXTURE_IPV4_ALT = '10.0.0.5'  # NOSONAR
_FIXTURE_IPV6 = 'fe80::1'  # NOSONAR
# Splash output uses plain http:// (Anthias serves the admin UI on
# plain HTTP per CLAUDE.md). Centralizing the literal here keeps
# Sonar's S5332 noise to a single suppression site instead of one
# per assertion.
_HTTP = 'http://'  # NOSONAR


# ---------------------------------------------------------------------------
# _format_ip_urls (pure formatter, no I/O)
# ---------------------------------------------------------------------------


def test_format_returns_empty_for_unknown_sentinel() -> None:
    """Balena first-boot mode: supervisor responded but with no IP."""
    assert v2_views._format_ip_urls('Unknown') == []


def test_format_returns_empty_for_unable_sentinel() -> None:
    """Bare-metal mode: host_agent didn't populate Redis in time."""
    assert v2_views._format_ip_urls('Unable to retrieve IP.') == []


def test_format_ipv4() -> None:
    assert v2_views._format_ip_urls(_FIXTURE_IPV4) == [
        f'{_HTTP}{_FIXTURE_IPV4}'
    ]


def test_format_ipv6_in_brackets() -> None:
    assert v2_views._format_ip_urls(_FIXTURE_IPV6) == [
        f'{_HTTP}[{_FIXTURE_IPV6}]'
    ]


def test_format_multiple_space_separated() -> None:
    assert v2_views._format_ip_urls(
        f'{_FIXTURE_IPV4} {_FIXTURE_IPV4_ALT}'
    ) == [f'{_HTTP}{_FIXTURE_IPV4}', f'{_HTTP}{_FIXTURE_IPV4_ALT}']


def test_format_drops_garbage_tokens() -> None:
    """Belt-and-suspenders: a malformed input must not crash a
    consumer. Valid IPs in the same string still pass through."""
    assert v2_views._format_ip_urls(
        f'not-an-ip {_FIXTURE_IPV4} also-garbage'
    ) == [f'{_HTTP}{_FIXTURE_IPV4}']


# ---------------------------------------------------------------------------
# _resolve_node_ip on bare metal (Redis cache fast path)
# ---------------------------------------------------------------------------
#
# Must never invoke ``anthias_common.utils.get_node_ip()``, which has a ~80s
# blocking readiness loop that would tail-back every poll.


@pytest.fixture
def bare_metal_no_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each test starts with a clean Redis fake (no debounce key set)
    and an unset ``MY_IP`` env var.

    ``_resolve_node_ip()`` falls back to ``MY_IP`` on the cache-miss
    path (mirroring ``anthias_common.utils.get_node_ip()``); leaving the env var
    in whatever state the dev shell or CI runner picked up would let
    that fallback bleed into tests that mean to assert on the
    no-cache-no-fallback case. Tests that exercise the MY_IP
    fallback set the env var explicitly via ``monkeypatch.setenv``.
    """
    v2_views.r.delete(v2_views._IP_REFRESH_PENDING_KEY)
    monkeypatch.delenv('MY_IP', raising=False)


def test_resolve_reads_from_redis_cache(bare_metal_no_pending: None) -> None:
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
        ),
        mock.patch.object(v2_views.r, 'publish'),
        mock.patch(
            'anthias_server.api.views.v2.get_node_ip',
            side_effect=AssertionError('must not block on get_node_ip'),
        ),
    ):
        assert v2_views._resolve_node_ip() == _FIXTURE_IPV4


def test_resolve_cache_hit_also_kicks_off_refresh(
    bare_metal_no_pending: None,
) -> None:
    """The splash docstring promises 'updates if IPs change during the
    splash's display window' (e.g. DHCP renewal mid-splash). Honoring
    that requires the cache-hit path to also fire a debounced refresh
    — without it, once Redis is populated the cached value would
    freeze for the rest of the display window."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
        ),
        mock.patch.object(v2_views.r, 'publish') as m_publish,
    ):
        assert v2_views._resolve_node_ip() == _FIXTURE_IPV4
    m_publish.assert_called_once_with('hostcmd', 'set_ip_addresses')


def test_resolve_publishes_refresh_on_cache_miss(
    bare_metal_no_pending: None,
) -> None:
    """Empty cache: return '' and ask host_agent to populate. The
    next poll picks it up. Don't block waiting for completion."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish') as m_publish,
        mock.patch(
            'anthias_server.api.views.v2.get_node_ip',
            side_effect=AssertionError('must not block on get_node_ip'),
        ),
    ):
        assert v2_views._resolve_node_ip() == ''
    m_publish.assert_called_once_with('hostcmd', 'set_ip_addresses')


def test_resolve_handles_malformed_cache_payload(
    bare_metal_no_pending: None,
) -> None:
    """Garbage in the cache (e.g. a partial write or a stray byte
    from another producer) must not crash the resolver."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value='not-valid-json'),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        assert v2_views._resolve_node_ip() == ''


def test_resolve_empty_list_in_cache_triggers_refresh(
    bare_metal_no_pending: None,
) -> None:
    """host_agent's first run on a still-coming-up network can write
    ``'[]'`` into the cache. If we treated that as a hit and returned
    '' without publishing, the splash would never recover when
    networking comes online during the splash window."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value='[]'),
        mock.patch.object(v2_views.r, 'publish') as m_publish,
    ):
        assert v2_views._resolve_node_ip() == ''
    m_publish.assert_called_once_with('hostcmd', 'set_ip_addresses')


def test_resolve_redis_get_failure_returns_empty(
    bare_metal_no_pending: None,
) -> None:
    """Redis flake during cache read must not 500 the splash poll.
    The polling endpoint is on a 2s loop — degrading to '' lets the
    JS keep polling until Redis recovers."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', side_effect=redis.RedisError('synthetic')
        ),
    ):
        assert v2_views._resolve_node_ip() == ''


def test_resolve_debounces_repeat_cache_miss_publishes(
    bare_metal_no_pending: None,
) -> None:
    """At a 2s poll cadence, host_agent.set_ip_addresses can take
    longer than one poll interval (its internal probe is a 10x1s
    tenacity retry). SETNX with TTL gates it: only the first call in
    the window publishes, later calls within the window no-op."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish') as m_publish,
    ):
        # Three cache-miss polls in rapid succession.
        v2_views._resolve_node_ip()
        v2_views._resolve_node_ip()
        v2_views._resolve_node_ip()
    # Only the first poll should have published.
    assert m_publish.call_count == 1


def test_resolve_publish_failure_releases_debounce(
    bare_metal_no_pending: None,
) -> None:
    """If the publish fails (Redis flake), the debounce key would
    otherwise pin us out of refreshing for the whole TTL — even
    though no refresh actually got requested. Clear the key on
    publish failure so the next poll can retry.

    Asserts on the ``delete`` call directly via ``wraps=`` rather than
    re-reading the key after the block: ``r.get`` is patched to
    always return ``None`` inside the block (so we can simulate the
    cache-miss path that triggers the publish), which would make a
    post-block ``r.get(_IP_REFRESH_PENDING_KEY) is None`` assertion
    pass for the wrong reason. ``wraps=`` lets the real
    ``r.delete`` run (so it actually clears the key in the fake
    store) while also recording the call, giving us a non-vacuous
    assertion that ``_publish_refresh`` invoked it on the right key.
    """
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(
            v2_views.r,
            'publish',
            side_effect=redis.RedisError('synthetic'),
        ) as m_publish,
        mock.patch.object(
            v2_views.r, 'delete', wraps=v2_views.r.delete
        ) as m_delete,
    ):
        v2_views._resolve_node_ip()

    # The publish was attempted (and raised), and ``_publish_refresh``
    # responded by calling ``r.delete`` on the debounce key to free
    # the next poll. Both halves of the contract are explicit here.
    m_publish.assert_called_once_with('hostcmd', 'set_ip_addresses')
    m_delete.assert_called_once_with(v2_views._IP_REFRESH_PENDING_KEY)


def test_resolve_setnx_failure_returns_empty(
    bare_metal_no_pending: None,
) -> None:
    """Redis can flake between the get() and the SETNX. Without a
    guard around SETNX, the polling endpoint would 500 — same 500
    we'd already prevented for get() and publish(). Treat as cache
    miss and let the JS keep polling."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(
            v2_views.r, 'set', side_effect=redis.RedisError('synthetic')
        ),
    ):
        assert v2_views._resolve_node_ip() == ''


def test_resolve_cache_miss_falls_back_to_my_ip(
    bare_metal_no_pending: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bin/upgrade_containers.sh`` exports the host's outbound IP
    into the server container as ``MY_IP``. ``anthias_common.utils.get_node_ip()``
    falls back to it when ``ip_addresses`` is empty in Redis. The
    polling resolver mirrors that — without the fallback, any setup
    where host_agent isn't running (custom deploys, late-starting
    host_agent, crashed host_agent) would freeze the splash on
    'Detecting network…' forever."""
    monkeypatch.setenv('MY_IP', _FIXTURE_IPV4)
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        assert v2_views._resolve_node_ip() == _FIXTURE_IPV4


def test_resolve_redis_get_failure_falls_back_to_my_ip(
    bare_metal_no_pending: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Redis is fully down (early boot, broker crash), the cache
    read raises before we can decide cache miss vs hit. The MY_IP
    fallback must still apply — that's exactly the scenario it's
    there to cover."""
    monkeypatch.setenv('MY_IP', _FIXTURE_IPV4)
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', side_effect=redis.RedisError('synthetic')
        ),
    ):
        assert v2_views._resolve_node_ip() == _FIXTURE_IPV4


def test_resolve_cache_miss_with_unset_my_ip_returns_empty(
    bare_metal_no_pending: None,
) -> None:
    """The fixture clears MY_IP. With no cache and no env fallback,
    we return '' so the JS keeps polling (and the splash continues
    to show 'Detecting network…' until something populates either
    side)."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        assert v2_views._resolve_node_ip() == ''


# ---------------------------------------------------------------------------
# _resolve_node_ip on Balena (bounded-timeout supervisor lookup)
# ---------------------------------------------------------------------------


def test_resolve_balena_reads_supervisor_response() -> None:
    fake_response = mock.Mock()
    fake_response.ok = True
    fake_response.json.return_value = {'ip_address': _FIXTURE_IPV4}
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=True
        ),
        mock.patch(
            'anthias_server.api.views.v2.get_balena_device_info',
            return_value=fake_response,
        ) as m_get,
    ):
        assert v2_views._resolve_node_ip() == _FIXTURE_IPV4
    # Tight timeout is the load-bearing part of this fix.
    timeout = m_get.call_args.kwargs.get('timeout')
    assert timeout is not None
    assert timeout <= 2.0


def test_resolve_balena_returns_empty_on_supervisor_timeout() -> None:
    """A slow first-boot supervisor must not block the endpoint."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=True
        ),
        mock.patch(
            'anthias_server.api.views.v2.get_balena_device_info',
            side_effect=requests.Timeout('synthetic'),
        ),
    ):
        assert v2_views._resolve_node_ip() == ''


def test_resolve_balena_returns_empty_on_supervisor_error_status() -> None:
    fake_response = mock.Mock()
    fake_response.ok = False
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=True
        ),
        mock.patch(
            'anthias_server.api.views.v2.get_balena_device_info',
            return_value=fake_response,
        ),
    ):
        assert v2_views._resolve_node_ip() == ''


# ---------------------------------------------------------------------------
# /api/v2/network/ip-addresses endpoint (full chain)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoint_returns_200_with_ip_list(
    bare_metal_no_pending: None,
) -> None:
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
        ),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        response = Client().get('/api/v2/network/ip-addresses')
    assert response.status_code == 200
    assert response.json() == {'ip_addresses': [f'{_HTTP}{_FIXTURE_IPV4}']}


@pytest.mark.django_db
def test_endpoint_returns_200_with_empty_list_on_cache_miss(
    bare_metal_no_pending: None,
) -> None:
    """Pinned regression: prior code 500'd when get_node_ip() returned
    'Unknown'. New code never raises — empty cache → []."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        response = Client().get('/api/v2/network/ip-addresses')
    assert response.status_code == 200
    assert response.json() == {'ip_addresses': []}


@pytest.mark.django_db
def test_endpoint_is_unauthenticated(bare_metal_no_pending: None) -> None:
    """The splash page itself is unauth'd and the data is already
    disclosed there, so the polling endpoint is unauth'd too. This
    test pins the choice — flipping it to @authorized would silently
    break the splash on auth-enabled installs."""
    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(
            v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
        ),
        mock.patch.object(v2_views.r, 'publish'),
    ):
        # No auth headers, no session — must still work.
        response = Client().get('/api/v2/network/ip-addresses')
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /splash-page render
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_splash_renders_200_without_mocking_anything() -> None:
    """Pinned regression: the prior render called
    ``ipaddress.ip_address(get_node_ip())`` and 500'd on 'Unknown'.
    The view now does no IP work at all — render must succeed with no
    fixtures, no mocks, no Redis state."""
    response = Client().get('/splash-page')
    assert response.status_code == 200


@pytest.mark.django_db
def test_splash_view_does_not_import_get_node_ip() -> None:
    """Belt-and-suspenders: assert the splash view module no longer
    carries get_node_ip in its namespace. A future refactor that
    re-imports it (and might re-introduce the synchronous IP work
    we just removed) would fail this test before any rendering
    regression hits production."""
    from anthias_server.app import views as splash_module

    assert not hasattr(splash_module, 'get_node_ip'), (
        'splash_page view should not import get_node_ip; IP resolution '
        'now lives in /api/v2/network/ip-addresses'
    )


@pytest.mark.django_db
def test_splash_renders_with_polling_script() -> None:
    """The splash relies on the JS poll to populate IPs. If the
    script tag goes missing (template refactor, CSP, etc.), the
    page would forever show 'Detecting network…' even when IPs
    are available — catch that here."""
    response = Client().get('/splash-page')
    body = response.content.decode()
    assert '/api/v2/network/ip-addresses' in body
    assert 'Detecting network' in body
