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
"""

from unittest import mock

from django.test import Client, TestCase

from api.views import v2 as v2_views


class SafeIpAddressesTest(TestCase):
    def test_returns_empty_when_node_ip_is_unknown(self) -> None:
        """Balena first-boot mode: supervisor responded but with no IP."""
        with mock.patch('api.views.v2.get_node_ip', return_value='Unknown'):
            self.assertEqual(v2_views._safe_ip_addresses(), [])

    def test_returns_empty_when_node_ip_is_unavailable_string(self) -> None:
        """Bare-metal mode: host_agent didn't populate Redis in time."""
        with mock.patch(
            'api.views.v2.get_node_ip', return_value='Unable to retrieve IP.'
        ):
            self.assertEqual(v2_views._safe_ip_addresses(), [])

    def test_formats_ipv4_as_http_url(self) -> None:
        with mock.patch(
            'api.views.v2.get_node_ip', return_value='192.168.1.42'
        ):
            self.assertEqual(
                v2_views._safe_ip_addresses(), ['http://192.168.1.42']
            )

    def test_formats_ipv6_in_brackets(self) -> None:
        with mock.patch('api.views.v2.get_node_ip', return_value='fe80::1'):
            self.assertEqual(
                v2_views._safe_ip_addresses(), ['http://[fe80::1]']
            )

    def test_returns_multiple_when_node_ip_is_space_separated(self) -> None:
        with mock.patch(
            'api.views.v2.get_node_ip',
            return_value='192.168.1.42 10.0.0.5',
        ):
            self.assertEqual(
                v2_views._safe_ip_addresses(),
                ['http://192.168.1.42', 'http://10.0.0.5'],
            )

    def test_silently_drops_garbage_tokens(self) -> None:
        """Belt-and-suspenders: a malformed get_node_ip() return must
        not crash a consumer. Valid IPs in the same string still pass
        through."""
        with mock.patch(
            'api.views.v2.get_node_ip',
            return_value='not-an-ip 192.168.1.42 also-garbage',
        ):
            self.assertEqual(
                v2_views._safe_ip_addresses(), ['http://192.168.1.42']
            )


class NetworkIpAddressesEndpointTest(TestCase):
    def test_returns_200_with_ip_list(self) -> None:
        with mock.patch(
            'api.views.v2.get_node_ip', return_value='192.168.1.42'
        ):
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {'ip_addresses': ['http://192.168.1.42']}
        )

    def test_returns_200_with_empty_list_when_unknown(self) -> None:
        """Pinned regression: 'Unknown' used to ValueError into a 500.
        Now the endpoint gracefully degrades to []."""
        with mock.patch('api.views.v2.get_node_ip', return_value='Unknown'):
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ip_addresses': []})

    def test_endpoint_is_unauthenticated(self) -> None:
        """The splash page itself is unauth'd and the data is already
        disclosed there, so the polling endpoint is unauth'd too. This
        test pins the choice — flipping it to @authorized would silently
        break the splash on auth-enabled installs."""
        with mock.patch(
            'api.views.v2.get_node_ip', return_value='192.168.1.42'
        ):
            # No auth headers, no session — must still work.
            response = Client().get('/api/v2/network/ip-addresses')
        self.assertEqual(response.status_code, 200)


class SplashPageViewTest(TestCase):
    def test_renders_200_even_when_node_ip_would_return_unknown(self) -> None:
        """Pinned regression: prior render called ipaddress.ip_address
        on the get_node_ip() string, which raised ValueError on
        'Unknown' and 500'd the page. The view no longer touches
        get_node_ip at all; this should work without any mocking."""
        # Belt-and-suspenders: assert the view doesn't call get_node_ip
        # synchronously even if some future refactor reintroduces an
        # import. The page is now JS-driven.
        with mock.patch(
            'api.views.v2.get_node_ip',
            side_effect=AssertionError('splash view must not call this'),
        ):
            response = Client().get('/splash-page')
        self.assertEqual(response.status_code, 200)

    def test_renders_with_polling_script(self) -> None:
        """The splash relies on the JS poll to populate IPs. If the
        script tag goes missing (template refactor, CSP, etc.), the
        page would forever show 'Detecting network…' even when IPs
        are available — catch that here."""
        response = Client().get('/splash-page')
        body = response.content.decode()
        self.assertIn('/api/v2/network/ip-addresses', body)
        self.assertIn('Detecting network', body)
