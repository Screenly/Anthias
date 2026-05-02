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

import requests
from django.test import Client, TestCase

from api.views import v2 as v2_views

_FIXTURE_IPV4 = '192.168.1.42'  # NOSONAR
_FIXTURE_IPV4_ALT = '10.0.0.5'  # NOSONAR
_FIXTURE_IPV6 = 'fe80::1'  # NOSONAR
# Splash output uses plain http:// (Anthias serves the admin UI on
# plain HTTP per CLAUDE.md). Centralizing the literal here keeps
# Sonar's S5332 noise to a single suppression site instead of one
# per assertion.
_HTTP = 'http://'  # NOSONAR


class FormatIpUrlsTest(TestCase):
    """The pure formatter — no resolver, no I/O. Same behavior is shared
    by the polling endpoint and ``/api/v2/info``; tested in one place.
    """

    def test_returns_empty_for_unknown_sentinel(self) -> None:
        """Balena first-boot mode: supervisor responded but with no IP."""
        self.assertEqual(v2_views._format_ip_urls('Unknown'), [])

    def test_returns_empty_for_unable_sentinel(self) -> None:
        """Bare-metal mode: host_agent didn't populate Redis in time."""
        self.assertEqual(
            v2_views._format_ip_urls('Unable to retrieve IP.'), []
        )

    def test_formats_ipv4(self) -> None:
        self.assertEqual(
            v2_views._format_ip_urls(_FIXTURE_IPV4),
            [f'{_HTTP}{_FIXTURE_IPV4}'],
        )

    def test_formats_ipv6_in_brackets(self) -> None:
        self.assertEqual(
            v2_views._format_ip_urls(_FIXTURE_IPV6),
            [f'{_HTTP}[{_FIXTURE_IPV6}]'],
        )

    def test_formats_multiple_space_separated(self) -> None:
        self.assertEqual(
            v2_views._format_ip_urls(f'{_FIXTURE_IPV4} {_FIXTURE_IPV4_ALT}'),
            [f'{_HTTP}{_FIXTURE_IPV4}', f'{_HTTP}{_FIXTURE_IPV4_ALT}'],
        )

    def test_drops_garbage_tokens(self) -> None:
        """Belt-and-suspenders: a malformed input must not crash a
        consumer. Valid IPs in the same string still pass through."""
        self.assertEqual(
            v2_views._format_ip_urls(
                f'not-an-ip {_FIXTURE_IPV4} also-garbage'
            ),
            [f'{_HTTP}{_FIXTURE_IPV4}'],
        )


class ResolveNodeIpBareMetalTest(TestCase):
    """Non-Balena fast-path: Redis cache read, fire-and-forget refresh
    on miss. Must never invoke ``lib.utils.get_node_ip()``, which has
    a ~80s blocking readiness loop that would tail-back every poll.
    """

    def test_reads_from_redis_cache(self) -> None:
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(
                v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
            ),
            mock.patch.object(v2_views.r, 'publish') as m_publish,
            mock.patch(
                'api.views.v2.get_node_ip',
                side_effect=AssertionError('must not block on get_node_ip'),
            ),
        ):
            self.assertEqual(v2_views._resolve_node_ip(), _FIXTURE_IPV4)
        # Cache hit — must not have triggered a refresh.
        m_publish.assert_not_called()

    def test_publishes_refresh_on_cache_miss(self) -> None:
        """Empty cache: return '' and ask host_agent to populate. The
        next poll picks it up. Don't block waiting for completion."""
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(v2_views.r, 'get', return_value=None),
            mock.patch.object(v2_views.r, 'publish') as m_publish,
            mock.patch(
                'api.views.v2.get_node_ip',
                side_effect=AssertionError('must not block on get_node_ip'),
            ),
        ):
            self.assertEqual(v2_views._resolve_node_ip(), '')
        m_publish.assert_called_once_with('hostcmd', 'set_ip_addresses')

    def test_handles_malformed_cache_payload(self) -> None:
        """Garbage in the cache (e.g. a partial write or a stray byte
        from another producer) must not crash the resolver."""
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(
                v2_views.r, 'get', return_value='not-valid-json'
            ),
            mock.patch.object(v2_views.r, 'publish'),
        ):
            self.assertEqual(v2_views._resolve_node_ip(), '')


class ResolveNodeIpBalenaTest(TestCase):
    """Balena fast-path: bounded-timeout supervisor lookup. The 1.5s
    cap keeps a slow supervisor from pinning a request worker for
    longer than the JS poll cadence (2s).
    """

    def test_reads_supervisor_response(self) -> None:
        fake_response = mock.Mock()
        fake_response.ok = True
        fake_response.json.return_value = {'ip_address': _FIXTURE_IPV4}
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=True),
            mock.patch(
                'api.views.v2.requests.get', return_value=fake_response
            ) as m_get,
        ):
            self.assertEqual(v2_views._resolve_node_ip(), _FIXTURE_IPV4)
        # Tight timeout is the load-bearing part of this fix.
        timeout = m_get.call_args.kwargs.get('timeout')
        self.assertIsNotNone(timeout)
        assert timeout is not None
        self.assertLessEqual(timeout, 2.0)

    def test_returns_empty_on_supervisor_timeout(self) -> None:
        """A slow first-boot supervisor must not block the endpoint."""
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=True),
            mock.patch(
                'api.views.v2.requests.get',
                side_effect=requests.Timeout('synthetic'),
            ),
        ):
            self.assertEqual(v2_views._resolve_node_ip(), '')

    def test_returns_empty_on_supervisor_error_status(self) -> None:
        fake_response = mock.Mock()
        fake_response.ok = False
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=True),
            mock.patch(
                'api.views.v2.requests.get', return_value=fake_response
            ),
        ):
            self.assertEqual(v2_views._resolve_node_ip(), '')


class NetworkIpAddressesEndpointTest(TestCase):
    """End-to-end through the polling endpoint. Goes through the full
    chain: HTTP routing, view, resolver, formatter."""

    def test_returns_200_with_ip_list(self) -> None:
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(
                v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
            ),
            mock.patch.object(v2_views.r, 'publish'),
        ):
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {'ip_addresses': [f'{_HTTP}{_FIXTURE_IPV4}']}
        )

    def test_returns_200_with_empty_list_on_cache_miss(self) -> None:
        """Pinned regression: prior code 500'd when get_node_ip() returned
        'Unknown'. New code never raises — empty cache → []."""
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(v2_views.r, 'get', return_value=None),
            mock.patch.object(v2_views.r, 'publish'),
        ):
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ip_addresses': []})

    def test_endpoint_is_unauthenticated(self) -> None:
        """The splash page itself is unauth'd and the data is already
        disclosed there, so the polling endpoint is unauth'd too. This
        test pins the choice — flipping it to @authorized would silently
        break the splash on auth-enabled installs."""
        with (
            mock.patch('api.views.v2.is_balena_app', return_value=False),
            mock.patch.object(
                v2_views.r, 'get', return_value=json.dumps([_FIXTURE_IPV4])
            ),
            mock.patch.object(v2_views.r, 'publish'),
        ):
            # No auth headers, no session — must still work.
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)


class SplashPageViewTest(TestCase):
    def test_renders_200_without_mocking_anything(self) -> None:
        """Pinned regression: the prior render called
        ``ipaddress.ip_address(get_node_ip())`` and 500'd on 'Unknown'.
        The view now does no IP work at all — render must succeed
        with no fixtures, no mocks, no Redis state."""
        response = Client().get('/splash-page')
        self.assertEqual(response.status_code, 200)

    def test_splash_view_does_not_import_get_node_ip(self) -> None:
        """Belt-and-suspenders: assert the splash view module no longer
        carries get_node_ip in its namespace. A future refactor that
        re-imports it (and might re-introduce the synchronous IP work
        we just removed) would fail this test before any rendering
        regression hits production. Asserting on the module attribute
        is more robust than mock.patch on a specific resolution path —
        the previous version of this test patched api.views.v2 by
        mistake and would have silently passed regardless of what the
        splash view did."""
        from anthias_app import views as splash_module

        self.assertFalse(
            hasattr(splash_module, 'get_node_ip'),
            'splash_page view should not import get_node_ip; '
            'IP resolution now lives in /api/v2/network/ip-addresses',
        )

    def test_renders_with_polling_script(self) -> None:
        """The splash relies on the JS poll to populate IPs. If the
        script tag goes missing (template refactor, CSP, etc.), the
        page would forever show 'Detecting network…' even when IPs
        are available — catch that here."""
        response = Client().get('/splash-page')
        body = response.content.decode()
        self.assertIn('/api/v2/network/ip-addresses', body)
        self.assertIn('Detecting network', body)
