"""Tests for the wifi-connect setup-portal integration.

The portal itself is a separate upstream binary running in its own
container, so what is testable here is the seam: how Anthias decides
the portal is live, how it reads the credentials out of the artefacts
the portal leaves on the shared volume, and what the /hotspot page
renders in each state.

The most valuable tests in here are the ones that pin the Python
constants to the compose templates. Those two sides have no compiler
between them: change ``PORTAL_LISTENING_PORT`` in a compose file and
nothing anywhere fails — the setup screen just quietly tells users to
open a port nothing is listening on.

The IP literals below are deliberate fixtures, and ``http://`` matches
how Anthias actually serves (plain HTTP per CLAUDE.md, TLS is the
opt-in Caddy sidecar). NOSONAR markers suppress S1313 / S5332 at the
constants; assertions inherit it by referencing them.
"""

import base64
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
import requests
from django.test import Client

from anthias_server.lib import wifi_connect

_FIXTURE_IPV4 = '192.168.1.42'  # NOSONAR
# One octet longer than the gateway, to catch a substring match.
_GATEWAY_PREFIX_TRAP = '192.168.42.10'  # NOSONAR

REPO_ROOT = Path(__file__).resolve().parent.parent

# Only the Debian/Raspberry Pi OS template ships the portal. The
# balena templates deliberately do not — there is no systemd there to
# hang the ExecCondition gate on, and an ungated access point across a
# fleet is not something to ship untested. Asserted by
# test_balena_does_not_ship_an_ungated_portal.
COMPOSE_TEMPLATES = ('docker-compose.yml.tmpl',)

BALENA_TEMPLATES = (
    'docker-compose.balena.yml.tmpl',
    'docker-compose.balena.dev.yml.tmpl',
)


# ---------------------------------------------------------------------------
# read_portal_state: wifi-connect's --state-file contract
# ---------------------------------------------------------------------------


def _service_block(body: str, service: str) -> str:
    """One service's block out of a compose template.

    The inline version of this appears in a few older tests; it is here
    because the balena assertions below check four separate things about
    one block and must not accidentally match a sibling service.
    """
    block = body.split(f'{service}:', 1)[1]
    # Up to the start of the next service / top-level key.
    return re.split(r'\n(?=\S|  \S+:\n)', block)[0]


def _state_document(**overrides: object) -> dict[str, object]:
    """A state file as wifi-connect 2026.10.8 writes one."""
    document: dict[str, object] = {
        'schema': 1,
        'portal_active': True,
        'ssid': 'Anthias Setup-G4VV',
        'security': 'wpa',
        'passphrase': 'brave-plum-42',
        'qr_payload': (
            'WIFI:T:WPA;S:Anthias Setup-G4VV;P:brave-plum-42;H:false;;'
        ),
        'portal_url': 'http://192.168.42.1:8000/',
        'gateway': '192.168.42.1',
        'port': 8000,
        'started_at_unix': int(time.time()),
        'heartbeat_unix': int(time.time()),
        'heartbeat_interval_s': 15,
    }
    document.update(overrides)
    return document


def _write_state(directory: Path, **overrides: object) -> None:
    (directory / wifi_connect.STATE_FILENAME).write_text(
        json.dumps(_state_document(**overrides))
    )


def test_reads_the_credentials_wifi_connect_published(tmp_path: Path) -> None:
    _write_state(tmp_path)

    portal = wifi_connect.read_portal_state(tmp_path)

    assert portal is not None
    assert portal.ssid == 'Anthias Setup-G4VV'
    assert portal.passphrase == 'brave-plum-42'
    assert portal.portal_url == 'http://192.168.42.1:8000/'


def test_no_state_file_means_no_portal_run(tmp_path: Path) -> None:
    """The ordinary case: a device that has never been offline.

    Also the case after a run ends -- wifi-connect removes the file on
    every exit, which is what makes its presence the liveness signal.
    """
    assert wifi_connect.read_portal_state(tmp_path) is None


def test_a_file_that_disclaims_being_active_is_not_a_portal(
    tmp_path: Path,
) -> None:
    """``portal_active`` is what makes a partial read fail closed.

    Parsing a file is not the same as a portal being up, and upstream
    writes the field explicitly so a consumer does not have to treat
    "this is valid JSON" as "someone can join a network right now".
    """
    _write_state(tmp_path, portal_active=False)

    assert wifi_connect.read_portal_state(tmp_path) is None


def test_an_open_portal_reports_no_passphrase(tmp_path: Path) -> None:
    """``--insecure-open-portal`` nulls the credential fields.

    The page must say the network is open, not render "None" at
    someone standing in front of it.
    """
    _write_state(tmp_path, security='open', passphrase=None, qr_payload=None)

    portal = wifi_connect.read_portal_state(tmp_path)

    assert portal is not None
    assert portal.ssid == 'Anthias Setup-G4VV'
    assert portal.passphrase == ''


def test_unknown_fields_are_ignored(tmp_path: Path) -> None:
    """Upstream adds fields without bumping the schema, by contract.

    A reader that refused them would turn every upstream release into
    a device that cannot show its own passphrase.
    """
    _write_state(tmp_path, schema=99, some_future_field={'nested': True})

    portal = wifi_connect.read_portal_state(tmp_path)

    assert portal is not None
    assert portal.ssid == 'Anthias Setup-G4VV'


def test_a_state_file_without_an_ssid_is_unusable(tmp_path: Path) -> None:
    """Nothing to tell the user to join."""
    _write_state(tmp_path, ssid='')

    assert wifi_connect.read_portal_state(tmp_path) is None


@pytest.mark.parametrize(
    'contents',
    [
        '',
        'not json at all',
        '[]',
        'null',
        '{"portal_active": true',
    ],
)
def test_a_malformed_state_file_is_not_an_error(
    tmp_path: Path, contents: str
) -> None:
    """The only page a stranded device can show must not 500."""
    (tmp_path / wifi_connect.STATE_FILENAME).write_text(contents)

    assert wifi_connect.read_portal_state(tmp_path) is None


def test_an_implausibly_large_state_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / wifi_connect.STATE_FILENAME).write_text(
        ' ' * (wifi_connect._MAX_STATE_BYTES + 1)
    )

    assert wifi_connect.read_portal_state(tmp_path) is None


def test_a_portal_that_stopped_beating_is_gone(tmp_path: Path) -> None:
    """The case the address check cannot see.

    SIGKILL the portal and it removes neither its state file nor its
    access point: NetworkManager keeps holding the gateway and the
    radio keeps broadcasting, so every other signal still reads as a
    healthy portal. Measured on a Pi 5. Only the heartbeat separates
    them.
    """
    _write_state(tmp_path, heartbeat_unix=int(time.time()) - 120)

    assert wifi_connect.read_portal_state(tmp_path) is None


def test_a_beat_or_two_may_be_missed(tmp_path: Path) -> None:
    """A loaded device must not blank its own setup screen."""
    _write_state(tmp_path, heartbeat_unix=int(time.time()) - 20)

    assert wifi_connect.read_portal_state(tmp_path) is not None


def test_the_staleness_bound_comes_from_the_file(tmp_path: Path) -> None:
    """Upstream may change its cadence; it says so in the file.

    A bound hardcoded here would turn a slower heartbeat upstream into
    a setup screen that blanks itself every few seconds.
    """
    _write_state(
        tmp_path,
        heartbeat_interval_s=600,
        heartbeat_unix=int(time.time()) - 300,
    )

    assert wifi_connect.read_portal_state(tmp_path) is not None


def test_a_state_file_with_no_heartbeat_is_trusted(tmp_path: Path) -> None:
    """Written by a wifi-connect older than the field.

    Presence was the whole contract then, and refusing it would blank
    the setup screen on a device whose portal is running fine.
    """
    document = _state_document()
    del document['heartbeat_unix']
    del document['heartbeat_interval_s']
    (tmp_path / wifi_connect.STATE_FILENAME).write_text(json.dumps(document))

    assert wifi_connect.read_portal_state(tmp_path) is not None


def test_anthias_does_not_probe_the_portal_listener() -> None:
    """The reason the state file exists at all.

    wifi-connect binds its listener to the gateway scoped to the
    wireless device, so a setup portal cannot answer over a customer's
    wired LAN -- and a packet from the Docker bridge is refused with
    it. A probe reintroduced here would pass every unit test in this
    file and fail on every device, so the import is what gets pinned.
    """
    source = (REPO_ROOT / 'src/anthias_server/lib/wifi_connect.py').read_text()

    assert 'requests' not in source
    assert 'urllib' not in source
    assert 'httpx' not in source


# ---------------------------------------------------------------------------
# portal_is_up / strip_portal_gateway
# ---------------------------------------------------------------------------


_DEFAULT_GW = wifi_connect.DEFAULT_PORTAL_GATEWAY


def test_portal_is_up_when_the_gateway_is_held() -> None:
    assert wifi_connect.portal_is_up(
        f'{_FIXTURE_IPV4} {_DEFAULT_GW}', _DEFAULT_GW
    )


def test_portal_is_down_without_the_gateway() -> None:
    assert not wifi_connect.portal_is_up(_FIXTURE_IPV4, _DEFAULT_GW)


def test_portal_is_down_for_an_empty_address_list() -> None:
    """A device whose addresses could not be resolved is not in setup."""
    assert not wifi_connect.portal_is_up('', _DEFAULT_GW)


def test_an_address_that_merely_starts_with_the_gateway_is_not_it() -> None:
    """Substring matching here would put a LAN device into setup mode."""
    assert not wifi_connect.portal_is_up(_GATEWAY_PREFIX_TRAP, _DEFAULT_GW)


def test_a_relocated_portal_is_found_where_it_says_it_is() -> None:
    """``--portal-gateway`` is the operator's to change.

    Detection follows the portal's own declaration, so a deployment
    that moved it is still detected -- and the default, which nothing
    now owns, is not mistaken for it.
    """
    addresses = f'{_FIXTURE_IPV4} 10.77.0.1'

    assert wifi_connect.portal_is_up(addresses, '10.77.0.1')
    assert not wifi_connect.portal_is_up(addresses, _DEFAULT_GW)


def test_strip_portal_gateway_removes_only_the_gateway() -> None:
    stripped = wifi_connect.strip_portal_gateway(
        f'{_FIXTURE_IPV4} {_DEFAULT_GW} {_GATEWAY_PREFIX_TRAP}', _DEFAULT_GW
    )

    assert stripped == f'{_FIXTURE_IPV4} {_GATEWAY_PREFIX_TRAP}'


def test_strip_portal_gateway_is_a_no_op_when_absent() -> None:
    assert (
        wifi_connect.strip_portal_gateway(_FIXTURE_IPV4, _DEFAULT_GW)
        == _FIXTURE_IPV4
    )


def test_a_lan_address_is_not_stripped_when_no_portal_owns_it() -> None:
    """The bug this exists for.

    A customer LAN really can be 192.168.42.0/24, and a device on it
    really can be handed .1. With nothing to say that address belonged
    to a setup portal, the splash page dropped it -- so the one screen
    telling an operator where to reach the device hid its only address,
    on a network where nothing was wrong.
    """
    addresses = f'{_DEFAULT_GW} {_FIXTURE_IPV4}'

    assert wifi_connect.strip_portal_gateway(addresses, None) == addresses


def test_a_relocated_portals_address_is_the_one_stripped() -> None:
    """And the default is left alone, because nothing owns it."""
    addresses = f'{_FIXTURE_IPV4} 10.77.0.1 {_DEFAULT_GW}'

    stripped = wifi_connect.strip_portal_gateway(addresses, '10.77.0.1')

    assert stripped == f'{_FIXTURE_IPV4} {_DEFAULT_GW}'


def test_nothing_in_the_server_hardcodes_the_setup_address() -> None:
    """The constant is a fallback for describing an absent portal.

    Anything deciding about a LIVE portal has to use the address the
    portal published, or a deployment that moved it silently stops
    being detected -- which looks like a screen that never shows its
    passphrase.
    """
    import ast

    module = ast.parse(
        (REPO_ROOT / 'src/anthias_server/lib/wifi_connect.py').read_text()
    )
    deciders = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {'portal_is_up', 'strip_portal_gateway'}
    ]
    assert len(deciders) == 2, 'both deciders should still be module-level'

    for node in deciders:
        # Skip the docstring: it explains the address, and explaining it
        # is not the same as depending on it.
        body = node.body[1:] if ast.get_docstring(node) else node.body
        code = '\n'.join(ast.dump(statement) for statement in body)

        assert 'DEFAULT_PORTAL_GATEWAY' not in code, node.name
        assert '192.168.42' not in code, node.name


# ---------------------------------------------------------------------------
# Reading the artefacts off the shared volume
# ---------------------------------------------------------------------------


def test_reads_the_qr_svg(tmp_path: Path) -> None:
    (tmp_path / wifi_connect.QR_SVG_FILENAME).write_text('<svg>qr</svg>')

    assert wifi_connect.read_qr_svg(tmp_path) == '<svg>qr</svg>'


def test_qr_data_uri_carries_the_svg_as_an_image(tmp_path: Path) -> None:
    svg = '<svg><script>alert(1)</script></svg>'
    (tmp_path / wifi_connect.QR_SVG_FILENAME).write_text(svg)

    uri = wifi_connect.read_qr_data_uri(tmp_path)

    assert uri is not None
    prefix = 'data:image/svg+xml;base64,'
    assert uri.startswith(prefix)
    assert base64.b64decode(uri.removeprefix(prefix)).decode() == svg


def test_missing_qr_gives_no_data_uri(tmp_path: Path) -> None:
    assert wifi_connect.read_qr_data_uri(tmp_path) is None


def test_missing_qr_svg_is_not_an_error(tmp_path: Path) -> None:
    assert wifi_connect.read_qr_svg(tmp_path) is None


def test_an_implausibly_large_svg_is_refused(tmp_path: Path) -> None:
    """The real file is ~4 KB; the page inlines whatever this returns."""
    (tmp_path / wifi_connect.QR_SVG_FILENAME).write_text(
        'x' * (wifi_connect._MAX_SVG_BYTES + 1)
    )

    assert wifi_connect.read_qr_svg(tmp_path) is None


# ---------------------------------------------------------------------------
# The compose templates and this module must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_pins_an_exact_wifi_connect_version(
    template_name: str,
) -> None:
    """Never ``latest`` or a floating month tag.

    An upstream patch reaching a fleet has to be a commit in this repo,
    not a push to a registry.
    """
    body = (REPO_ROOT / template_name).read_text()
    tags = re.findall(
        r'ghcr\.io/screenly-labs/wifi-connect:(\S+)',
        body,
    )

    assert tags, f'{template_name} does not run wifi-connect'
    for tag in tags:
        assert re.fullmatch(r'\d{4}\.\d{2}\.\d+', tag), (
            f'{template_name} pins wifi-connect to {tag!r}; it must be an '
            f'exact CalVer release'
        )


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_portal_port_matches_this_module(template_name: str) -> None:
    body = (REPO_ROOT / template_name).read_text()

    assert (
        f'PORTAL_LISTENING_PORT={wifi_connect.DEFAULT_PORTAL_PORT}' in body
    ), (
        f'{template_name} and wifi_connect.DEFAULT_PORTAL_PORT disagree about the '
        f'portal port; the setup screen would tell users to open the wrong '
        f'one'
    )


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_qr_directory_matches_this_module(
    template_name: str,
) -> None:
    body = (REPO_ROOT / template_name).read_text()

    assert f'WIFI_CONNECT_QR_OUTPUT={wifi_connect.QR_DIRECTORY}' in body, (
        f'{template_name} writes the join QR somewhere this container does '
        f'not read it from'
    )

    # Same hazard, worse failure: a state file written somewhere this
    # container does not look means every portal run reads as "no
    # portal", and the screen never shows a passphrase.
    state_path = wifi_connect.QR_DIRECTORY / wifi_connect.STATE_FILENAME
    assert f'WIFI_CONNECT_STATE_FILE={state_path}' in body, (
        f'{template_name} and anthias_server.lib.wifi_connect disagree '
        f'about where the portal state file lives'
    )


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_does_not_override_the_portal_gateway(
    template_name: str,
) -> None:
    """``PORTAL_GATEWAY`` is the liveness signal, so it stays default.

    Overriding it in a template without updating ``PORTAL_GATEWAY``
    here would leave the device unable to tell that its own portal is
    running.
    """
    body = (REPO_ROOT / template_name).read_text()

    assert 'PORTAL_GATEWAY=' not in body


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_bounds_the_portal_with_an_activity_timeout(
    template_name: str,
) -> None:
    """The safety valve against a misreported connectivity state.

    ``ACTIVITY_TIMEOUT=0`` means "never give up", which on a device
    whose NetworkManager connectivity check is blocked turns a false
    offline reading into a permanent outage.
    """
    body = (REPO_ROOT / template_name).read_text()
    timeouts = re.findall(r'ACTIVITY_TIMEOUT=(\d+)', body)

    assert timeouts, f'{template_name} does not bound the portal'
    for timeout in timeouts:
        assert int(timeout) > 0


@pytest.mark.parametrize('template_name', COMPOSE_TEMPLATES)
def test_compose_does_not_restart_wifi_connect(template_name: str) -> None:
    """One run per boot, never a loop.

    wifi-connect exits 0 as soon as it finds the device online, so a
    restart policy re-runs it forever — and re-raises the access point
    every ACTIVITY_TIMEOUT on a device whose connectivity check is
    blocked. ansible's anthias-wifi-connect.service is what starts the
    one run each boot gets.
    """
    body = (REPO_ROOT / template_name).read_text()
    service = body.split('anthias-wifi-connect:', 1)[1]
    # Up to the start of the next service / top-level key.
    service = re.split(r'\n(?=\S|  \S+:\n)', service)[0]

    assert re.search(r'restart:\s*"no"', service), (
        f'{template_name} gives anthias-wifi-connect a restart policy'
    )


UNIT_PATH = 'ansible/roles/network/templates/anthias-wifi-connect.service'


def test_the_systemd_unit_starts_the_compose_container() -> None:
    """The unit and the compose service must name the same container."""
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert 'docker start --attach anthias-wifi-connect' in unit
    assert (
        'anthias-wifi-connect:'
        in (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    )


def test_the_systemd_unit_does_not_time_out_a_portal_session() -> None:
    """``Type=oneshot`` here would kill the portal after 90 seconds.

    A session legitimately runs to ACTIVITY_TIMEOUT, which is far
    longer than DefaultTimeoutStartSec — and a oneshot unit is killed
    at that bound, mid-setup, every time.
    """
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert re.search(r'^Type=exec$', unit, re.MULTILINE)
    assert not re.search(r'^Type=oneshot$', unit, re.MULTILINE)


def test_stopping_the_unit_brings_the_access_point_down() -> None:
    """SIGTERM to ``docker start --attach`` only detaches the CLI.

    Without an explicit stop, ``systemctl stop`` would leave the
    access point broadcasting with nothing supervising it.
    """
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert 'docker stop' in unit
    assert re.search(r'^ExecStop=', unit, re.MULTILINE)


def test_a_wired_device_is_not_a_failed_unit() -> None:
    """Exit 3 is "no WiFi adapter" — every ethernet-only x86 box."""
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    statuses = re.search(r'^SuccessExitStatus=(.+)$', unit, re.MULTILINE)

    assert statuses is not None
    assert '3' in statuses.group(1).split()


def test_the_unit_does_not_restart_the_portal() -> None:
    """The timer owns the cadence; a Restart= here would be immediate."""
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert re.search(r'^Restart=no$', unit, re.MULTILINE)


TIMER_PATH = 'ansible/roles/network/templates/anthias-wifi-connect.timer'


def test_the_check_repeats_rather_than_running_once_per_boot() -> None:
    """Both predecessors re-checked; a single shot strands a screen.

    Anthias's own pre-2026.04 implementation looped on
    CHECK_CONN_FREQ until the device came online. Without a repeat, a
    screen that nobody sets up inside one portal window — or whose
    router is replaced later — has no way back short of a reboot.
    """
    timer = (REPO_ROOT / TIMER_PATH).read_text()

    assert re.search(r'^OnBootSec=', timer, re.MULTILINE)
    assert re.search(r'^OnUnitInactiveSec=', timer, re.MULTILINE)


def test_the_repeat_measures_from_the_end_of_the_last_check() -> None:
    """OnCalendar/OnActiveSec would fire mid-portal-session."""
    timer = (REPO_ROOT / TIMER_PATH).read_text()

    assert not re.search(r'^OnCalendar=', timer, re.MULTILINE)


def test_the_service_cannot_be_enabled_without_the_timer() -> None:
    """No [Install] on the service: the timer is the only entry point.

    Enabling both would start the service at boot independently of
    the timer, so a skipped check would never be retried.
    """
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    # Anchored: the comment above the [Service] block explains why the
    # section is absent, and a substring check would match that.
    assert not re.search(r'^\[Install\]$', unit, re.MULTILINE)
    assert re.search(
        r'^\[Install\]$', (REPO_ROOT / TIMER_PATH).read_text(), re.MULTILINE
    )


def test_ansible_enables_the_timer_not_the_service() -> None:
    tasks = (REPO_ROOT / 'ansible/roles/network/tasks/main.yml').read_text()

    assert 'name: anthias-wifi-connect.timer\n    enabled: true' in tasks
    assert 'name: anthias-wifi-connect.service\n    enabled: true' not in tasks


def test_the_gate_checks_what_both_predecessors_checked() -> None:
    """Union of balena's condition and Anthias's own.

    balena gates on an active WiFi association (`iwgetid -r`);
    Anthias's pre-2026.04 script gated on an internet probe. Either
    alone is wrong here — see the script's own header — so the gate
    has to cover WiFi association, a working wired link, and internet.
    """
    body = (REPO_ROOT / GATE_PATH).read_text()

    assert 'wifi_is_associated' in body
    assert 'wired_is_connected' in body
    assert 'internet_is_reachable' in body


def test_the_gate_ignores_the_portals_own_access_point() -> None:
    """Otherwise a running portal reads as "WiFi is configured"."""
    body = (REPO_ROOT / GATE_PATH).read_text()

    assert 'wifi-connect-portal' in body


def test_upgrade_script_no_longer_reaps_the_live_container() -> None:
    """``docker rm -f`` on the running portal would tear it down.

    anthias-wifi-connect is a current compose service again; only the
    pre-rebrand name belongs in the legacy sweep.
    """
    body = (REPO_ROOT / 'bin/upgrade_containers.sh').read_text()
    rm_command = body.split('docker rm -f', 1)[1].split('>/dev/null', 1)[0]

    assert 'srly-ose-wifi-connect' in rm_command
    assert 'anthias-wifi-connect' not in rm_command.replace(
        'srly-ose-wifi-connect', ''
    )


# ---------------------------------------------------------------------------
# The /hotspot page
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.mark.django_db
def test_hotspot_page_shows_the_credentials_while_setup_runs(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'anthias_server.app.views.wifi_setup_state',
        lambda: {
            'active': True,
            'ssid': 'Anthias Setup',
            'passphrase': 'hunter2hunter2',
            'portal_url': wifi_connect.PORTAL_URL,
        },
    )
    monkeypatch.setattr(
        'anthias_server.app.views.read_qr_data_uri',
        lambda: 'data:image/svg+xml;base64,PHN2Zy8+',
    )

    response = client.get('/hotspot/')
    body = response.content.decode()

    assert response.status_code == 200
    assert 'Anthias Setup' in body
    assert 'hunter2hunter2' in body
    assert wifi_connect.PORTAL_URL in body
    # A data: URI, not a link: the page has to render on a device with
    # no working network, so it gets one request. An <img>, not inline
    # markup, so nothing in the SVG can run.
    assert 'src="data:image/svg+xml;base64,PHN2Zy8+"' in body
    assert '<svg' not in body


@pytest.mark.django_db
def test_hotspot_page_withholds_the_passphrase_when_idle(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An online device must not serve a setup passphrase unauth'd."""
    monkeypatch.setattr(
        'anthias_server.app.views.wifi_setup_state',
        lambda: {
            'active': False,
            'ssid': None,
            'passphrase': None,
            'portal_url': wifi_connect.PORTAL_URL,
        },
    )
    monkeypatch.setattr(
        'anthias_server.app.views.read_qr_data_uri',
        lambda: (_ for _ in ()).throw(
            AssertionError('must not read the QR when setup is not running')
        ),
    )

    response = client.get('/hotspot/')
    body = response.content.decode()

    assert response.status_code == 200
    assert 'Nothing to set up' in body


@pytest.mark.django_db
def test_hotspot_page_needs_no_credentials(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The viewer has no session, and this is the screen it must show."""
    monkeypatch.setattr(
        'anthias_server.app.views.wifi_setup_state',
        lambda: {
            'active': False,
            'ssid': None,
            'passphrase': None,
            'portal_url': wifi_connect.PORTAL_URL,
        },
    )

    response = client.get('/hotspot/')

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The viewer side: deciding when to put the setup screen on the display
# ---------------------------------------------------------------------------


@pytest.fixture
def viewer_wifi_state() -> Iterator[None]:
    """Reset the viewer's wifi-setup globals around each test."""
    import anthias_viewer as viewer

    viewer._wifi_setup_active = False
    viewer._wifi_setup_logged = False
    yield
    viewer._wifi_setup_active = False
    viewer._wifi_setup_logged = False


def test_viewer_goes_active_when_the_server_says_so(
    viewer_wifi_state: None,
) -> None:
    import anthias_viewer as viewer

    with mock.patch(
        'anthias_viewer.requests.get',
        return_value=mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(return_value={'active': True}),
        ),
    ):
        viewer._poll_wifi_setup_once()

    assert viewer._wifi_setup_active


def test_viewer_leaves_setup_when_the_portal_comes_down(
    viewer_wifi_state: None,
) -> None:
    import anthias_viewer as viewer

    viewer._wifi_setup_active = True
    viewer._wifi_setup_logged = True

    with mock.patch(
        'anthias_viewer.requests.get',
        return_value=mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(return_value={'active': False}),
        ),
    ):
        viewer._poll_wifi_setup_once()

    assert not viewer._wifi_setup_active
    # Re-armed, so a later portal logs its own edge.
    assert not viewer._wifi_setup_logged


def test_an_unreachable_server_does_not_strand_the_setup_screen(
    viewer_wifi_state: None,
) -> None:
    """Failing towards playback is the safe direction.

    A restarting anthias-server must not leave a device showing setup
    instructions for an access point that stopped broadcasting ten
    minutes ago.
    """
    import anthias_viewer as viewer

    viewer._wifi_setup_active = True

    with mock.patch(
        'anthias_viewer.requests.get',
        side_effect=requests.exceptions.ConnectionError(),
    ):
        viewer._poll_wifi_setup_once()

    assert not viewer._wifi_setup_active


def test_a_non_json_response_does_not_strand_the_setup_screen(
    viewer_wifi_state: None,
) -> None:
    import anthias_viewer as viewer

    viewer._wifi_setup_active = True

    with mock.patch(
        'anthias_viewer.requests.get',
        return_value=mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(side_effect=ValueError('not json')),
        ),
    ):
        viewer._poll_wifi_setup_once()

    assert not viewer._wifi_setup_active


def test_setup_screen_preempts_the_playlist(
    viewer_wifi_state: None,
) -> None:
    """The scheduler must not even be asked while setup is running.

    A device in setup has no network, so anything it would play is
    either stale or unreachable — and the passphrase on this screen is
    the only way out of that state.
    """
    import anthias_viewer as viewer

    viewer._wifi_setup_active = True
    scheduler = mock.Mock()
    scheduler.get_next_asset.side_effect = AssertionError(
        'the playlist must not be consulted during WiFi setup'
    )

    with (
        mock.patch.object(viewer, 'view_webpage') as view_webpage,
        mock.patch.object(viewer, 'sleep'),
        mock.patch.object(viewer, '_consume_pending_rotation_bounce'),
        mock.patch.object(viewer, '_retry_wayland_rotation_if_pending'),
        mock.patch.object(viewer, '_wayland_output_watchdog'),
    ):
        viewer.asset_loop(scheduler)

    view_webpage.assert_called_once_with(viewer.HOTSPOT_PAGE_URL)


def test_playlist_runs_normally_when_setup_is_not_active(
    viewer_wifi_state: None,
) -> None:
    import anthias_viewer as viewer

    scheduler = mock.Mock()
    scheduler.get_next_asset.return_value = None

    with (
        mock.patch.object(viewer, 'view_webpage') as view_webpage,
        mock.patch.object(viewer, 'view_image'),
        mock.patch.object(viewer, 'get_skip_event'),
        mock.patch.object(viewer, '_consume_pending_rotation_bounce'),
        mock.patch.object(viewer, '_retry_wayland_rotation_if_pending'),
        mock.patch.object(viewer, '_wayland_output_watchdog'),
    ):
        viewer.asset_loop(scheduler)

    scheduler.get_next_asset.assert_called_once()
    view_webpage.assert_not_called()


# ---------------------------------------------------------------------------
# The refresh the WiFi-setup poll drives on the host
# ---------------------------------------------------------------------------
#
# This poll runs for the whole life of an offline device, unlike the
# splash's, which runs for 60 seconds at boot. Reusing the splash's
# refresh would put host_agent inside a retrying outbound HTTP call for
# ~10 of every 15 seconds -- and host_agent handles reboot/shutdown on
# that same single thread.


def test_wifi_setup_poll_asks_for_the_probe_free_refresh() -> None:
    from anthias_server.api.views import v2 as v2_views

    v2_views.r.delete(v2_views._WIFI_SETUP_REFRESH_PENDING_KEY)

    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish') as publish,
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=wifi_connect.PortalState(
                'Anthias Setup-G4VV',
                'p',
                wifi_connect.PORTAL_URL,
                wifi_connect.DEFAULT_PORTAL_GATEWAY,
                'wlan0',
            ),
        ),
    ):
        v2_views.wifi_setup_state()

    publish.assert_called_once_with('hostcmd', 'set_ip_addresses_fast')


def test_a_device_with_no_portal_does_not_wake_host_agent() -> None:
    """The cheap signal is checked first, and it is local.

    This poll runs every few seconds for the whole life of a device.
    Before the state file there was nothing local to ask, so every
    tick published a refresh command to host_agent -- on every healthy
    screen in a fleet, forever. Now the host is only disturbed once
    something on disk says a portal run is live.
    """
    from anthias_server.api.views import v2 as v2_views

    v2_views.r.delete(v2_views._WIFI_SETUP_REFRESH_PENDING_KEY)

    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish') as publish,
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=None,
        ),
    ):
        assert v2_views.wifi_setup_state()['active'] is False

    publish.assert_not_called()


def test_splash_poll_still_asks_for_the_probing_refresh() -> None:
    """The splash's own behaviour must be untouched by the above."""
    from anthias_server.api.views import v2 as v2_views

    v2_views.r.delete(v2_views._IP_REFRESH_PENDING_KEY)

    with (
        mock.patch(
            'anthias_server.api.views.v2.is_balena_app', return_value=False
        ),
        mock.patch.object(v2_views.r, 'get', return_value=None),
        mock.patch.object(v2_views.r, 'publish') as publish,
    ):
        v2_views._resolve_node_ip()

    publish.assert_called_once_with('hostcmd', 'set_ip_addresses')


def test_the_two_refreshes_do_not_share_a_debounce() -> None:
    """One poll must not be able to starve the other."""
    from anthias_server.api.views import v2 as v2_views

    assert (
        v2_views._WIFI_SETUP_REFRESH_PENDING_KEY
        != v2_views._IP_REFRESH_PENDING_KEY
    )


def test_the_fast_host_refresh_does_no_internet_probe() -> None:
    """host_agent's single thread must not sit in an HTTP retry loop."""
    from anthias_host_agent import __main__ as host_agent

    with (
        mock.patch.object(host_agent, 'redis') as fake_redis,
        mock.patch.object(
            host_agent,
            'get_ip_addresses',
            return_value=[wifi_connect.DEFAULT_PORTAL_GATEWAY],
        ),
        mock.patch(
            'anthias_host_agent.__main__.requests.get',
            side_effect=AssertionError('must not probe the internet'),
        ),
    ):
        host_agent.set_ip_addresses(probe_internet=False)

    rdb = fake_redis.Redis.return_value
    rdb.set.assert_any_call('ip_addresses_ready', 'true')
    rdb.set.assert_any_call(
        'ip_addresses', f'["{wifi_connect.DEFAULT_PORTAL_GATEWAY}"]'
    )


def test_the_fast_host_refresh_is_reachable_as_a_host_command() -> None:
    """The server publishes this name; host_agent must recognise it."""
    from anthias_host_agent import __main__ as host_agent

    assert 'set_ip_addresses_fast' in host_agent.CMD_TO_ARGV


# ---------------------------------------------------------------------------
# Liveness: two signals, each covering the other's blind spot
# ---------------------------------------------------------------------------
#
# The state file says a portal run is live and carries the
# credentials, but a SIGKILLed portal cannot remove its own file.
#
# The address list cannot be left behind like that -- NetworkManager
# drops the gateway the moment the access point comes down -- but it
# is a cache. Found on the Pi 5 testbed: host_agent stopped, the list
# froze with the access point's address still in it, and /hotspot
# stayed full-screen for a portal that had been down for minutes.
#
# Requiring both is what makes a stale answer in either direction read
# as "not running", which fails towards playing content.


def test_a_stale_address_cache_does_not_report_setup_mode() -> None:
    """Gateway still cached, no portal run: not setup mode."""
    from anthias_server.api.views import v2 as v2_views

    with (
        mock.patch.object(
            v2_views,
            '_resolve_node_ip',
            return_value=f'{_FIXTURE_IPV4} {wifi_connect.DEFAULT_PORTAL_GATEWAY}',
        ),
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=None,
        ),
    ):
        state = v2_views.wifi_setup_state()

    assert state['active'] is False
    assert state['passphrase'] is None


def test_a_state_file_left_behind_does_not_report_setup_mode() -> None:
    """The other direction: a SIGKILLed portal cannot clean up.

    Nothing on the device would ever remove that file, so without the
    address check a screen would show a dead portal's passphrase until
    the next run.
    """
    from anthias_server.api.views import v2 as v2_views

    with (
        mock.patch.object(
            v2_views, '_resolve_node_ip', return_value=_FIXTURE_IPV4
        ),
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=wifi_connect.PortalState(
                'Anthias Setup-G4VV',
                'p',
                wifi_connect.PORTAL_URL,
                wifi_connect.DEFAULT_PORTAL_GATEWAY,
                'wlan0',
            ),
        ),
    ):
        state = v2_views.wifi_setup_state()

    assert state['active'] is False
    assert state['passphrase'] is None


def test_a_live_portal_still_reports_setup_mode() -> None:
    from anthias_server.api.views import v2 as v2_views

    with (
        mock.patch.object(
            v2_views,
            '_resolve_node_ip',
            return_value=f'{_FIXTURE_IPV4} {wifi_connect.DEFAULT_PORTAL_GATEWAY}',
        ),
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=wifi_connect.PortalState(
                'Anthias Setup-G4VV',
                'brave-plum-42',
                'http://192.168.42.1:8000/',
                wifi_connect.DEFAULT_PORTAL_GATEWAY,
                'wlan0',
            ),
        ),
    ):
        state = v2_views.wifi_setup_state()

    assert state['active'] is True
    assert state['ssid'] == 'Anthias Setup-G4VV'
    assert state['passphrase'] == 'brave-plum-42'
    assert state['portal_url'] == 'http://192.168.42.1:8000/'


def test_the_ssid_shown_is_the_one_on_the_air() -> None:
    """wifi-connect appends a per-device suffix to PORTAL_SSID.

    The screen must show what the phone will list, not the prefix out
    of the compose file -- that is the whole point of the suffix.
    """
    from anthias_server.api.views import v2 as v2_views

    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    match = re.search(r'- PORTAL_SSID=(.+)', compose)
    assert match is not None, 'the service block must still set PORTAL_SSID'
    configured = match.group(1).strip()

    with (
        mock.patch.object(
            v2_views,
            '_resolve_node_ip',
            return_value=f'{_FIXTURE_IPV4} {wifi_connect.DEFAULT_PORTAL_GATEWAY}',
        ),
        mock.patch(
            'anthias_server.lib.wifi_connect.read_portal_state',
            return_value=wifi_connect.PortalState(
                f'{configured}-G4VV',
                'p',
                wifi_connect.PORTAL_URL,
                wifi_connect.DEFAULT_PORTAL_GATEWAY,
                'wlan0',
            ),
        ),
    ):
        state = v2_views.wifi_setup_state()

    assert state['ssid'] == f'{configured}-G4VV'
    assert state['ssid'] != configured


def test_host_agent_no_longer_probes_the_portal() -> None:
    """Deleted along with the probe it published for.

    wifi-connect reports its own state now, so the host-side HTTP
    probe and the Redis key it wrote are gone. Keeping a dead key
    around invites something to start reading it again.
    """
    from anthias_host_agent import __main__ as host_agent

    source = (REPO_ROOT / 'src/anthias_host_agent/__main__.py').read_text()

    assert not hasattr(host_agent, 'set_portal_serving')
    assert 'wifi_setup_portal_serving' not in source
    assert 'captive-portal' not in source


def test_the_two_sides_agree_on_the_gateway() -> None:
    """Two processes, one string, no compiler between them."""
    from anthias_host_agent import __main__ as host_agent

    assert not hasattr(host_agent, 'PORTAL_GATEWAY')


# ---------------------------------------------------------------------------
# The host-side connectivity gate
# ---------------------------------------------------------------------------
#
# NetworkManager on every Anthias platform is built without
# connectivity-check support, so wifi-connect's own gate always sees
# "unknown" and raises the portal. Measured on the whole QA fleet.
# Anthias has to decide instead, or every device raises an access
# point on every boot.

GATE_PATH = 'bin/wifi_connect_needed.sh'


def test_the_unit_gates_on_connectivity_before_running() -> None:
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert 'ExecCondition=' in unit
    assert 'wifi_connect_needed.sh' in unit


def test_the_gate_uses_exec_condition_not_exec_start_pre() -> None:
    """A failed ExecStartPre marks the unit FAILED on every online boot.

    ExecCondition's clean non-zero means "skip", which is what an
    online device should look like.
    """
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert not re.search(
        r'^ExecStartPre=.*wifi_connect_needed', unit, re.MULTILINE
    )
    assert re.search(
        r'^ExecCondition=.*wifi_connect_needed', unit, re.MULTILINE
    )


def test_the_gate_script_is_executable_and_shipped() -> None:
    gate = REPO_ROOT / GATE_PATH

    assert gate.exists()
    assert gate.stat().st_mode & 0o111, 'gate script is not executable'


def test_the_gate_honours_a_proxy() -> None:
    """A proxied device must not be judged offline by a blocked probe."""
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    assert 'EnvironmentFile=-/etc/anthias/proxy.env' in unit


def test_the_gate_retries_before_declaring_a_device_offline() -> None:
    """A boot can outrun DHCP; one failed probe must not raise an AP."""
    body = (REPO_ROOT / GATE_PATH).read_text()

    attempts = re.search(r'^ATTEMPTS=(\d+)$', body, re.MULTILINE)

    assert attempts is not None
    assert int(attempts.group(1)) > 1


# ---------------------------------------------------------------------------
# The gate has to be the ONLY way the portal starts
# ---------------------------------------------------------------------------
#
# `restart: "no"` governs restart-on-exit, not the initial start, so a
# plain `docker compose up -d` would walk straight past the
# ExecCondition and raise an access point on an online device — on
# every upgrade.


def test_the_portal_service_is_behind_a_compose_profile() -> None:
    body = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    service = body.split('anthias-wifi-connect:', 1)[1]
    service = re.split(r'\n(?=\S|  \S+:\n)', service)[0]

    assert re.search(r'^\s+profiles:', service, re.MULTILINE), (
        'anthias-wifi-connect has no compose profile, so `docker compose '
        'up -d` starts it without the ExecCondition gate'
    )


def test_the_upgrade_script_creates_it_without_starting_it() -> None:
    """The unit uses `docker start`, so the container must exist."""
    body = (REPO_ROOT / 'bin/upgrade_containers.sh').read_text()

    assert '--profile wifi-connect' in body
    assert '--no-start anthias-wifi-connect' in body


def test_balena_ships_the_portal() -> None:
    """A screen that cannot reach a network needs this everywhere.

    balena has no systemd, so there is no ExecCondition and no compose
    profile keeping it back: the supervisor starts it on every boot and
    every release apply. What makes that safe is the daemon deciding
    for itself -- see the pin assertion below.
    """
    for template_name in BALENA_TEMPLATES:
        body = (REPO_ROOT / template_name).read_text()
        services = re.findall(r'^  ([a-z0-9-]+):$', body, re.MULTILINE)

        assert 'anthias-wifi-connect' in services, (
            f'{template_name} does not ship the setup portal'
        )


def test_balena_pins_a_wifi_connect_that_gates_itself() -> None:
    """The pin and the lack of a gate are one decision.

    Before 2026.10.11 a device online over ethernet alone did not count
    as online -- NetworkManager answers "unknown" wherever its
    connectivity check is unavailable, and only an associated radio
    counted -- so every supervisor start raised an access point at a
    healthy screen. On balena that is a fleet, on every release apply.

    Verified on a wired Pi 5 and Pi 3 B+: 2026.10.11 exits 0 with
    "already online; the portal is not needed"; 2026.10.10 raises one.
    """
    for template_name in BALENA_TEMPLATES:
        body = (REPO_ROOT / template_name).read_text()
        pin = re.search(
            r'ghcr\.io/screenly-labs/wifi-connect:(\d{4})\.(\d{2})\.(\d+)',
            body,
        )

        assert pin is not None, f'{template_name} does not pin the portal'
        assert tuple(int(g) for g in pin.groups()) >= (2026, 10, 11), (
            f'{template_name} pins a wifi-connect that raises an access '
            f'point at a wired device on every start'
        )


def test_balena_does_not_let_the_supervisor_restart_the_portal() -> None:
    """It exits 0 the moment the device is online, which is immediately
    on a healthy screen. A restarting service would churn a container
    start a minute forever, and on a device whose connectivity cannot be
    determined would re-raise the access point every ACTIVITY_TIMEOUT.
    """
    for template_name in BALENA_TEMPLATES:
        service = _service_block(
            (REPO_ROOT / template_name).read_text(), 'anthias-wifi-connect'
        )

        assert re.search(r'^\s+restart:\s*"no"', service, re.MULTILINE), (
            f'{template_name} lets the supervisor restart the portal'
        )


def test_balena_gives_the_portal_the_host_bus_and_the_shared_volume() -> None:
    """Two things it cannot work without.

    NetworkManager is on the host, so without the dbus feature label the
    daemon exits 5 before doing anything. And the state file is how
    anthias-server learns the SSID and passphrase to put on the screen --
    without the shared volume the portal comes up and the display has
    nothing to show, which is the same as not working.
    """
    for template_name in BALENA_TEMPLATES:
        service = _service_block(
            (REPO_ROOT / template_name).read_text(), 'anthias-wifi-connect'
        )

        assert "io.balena.features.dbus: '1'" in service, template_name
        assert '/host/run/dbus/system_bus_socket' in service, template_name
        assert 'resin-data:/data' in service, template_name

        state_path = wifi_connect.QR_DIRECTORY / wifi_connect.STATE_FILENAME
        assert f'WIFI_CONNECT_STATE_FILE={state_path}' in service, (
            f'{template_name} and the server disagree about the state file'
        )


def test_a_timed_out_portal_is_not_a_failed_unit() -> None:
    """Upstream's Outcome::TimedOut is 6, and it is the normal ending.

    ACTIVITY_TIMEOUT elapsing with nobody completing setup is what
    happens every time a screen is left alone; a unit that goes
    `failed` each time teaches operators to ignore its state.
    """
    unit = (REPO_ROOT / UNIT_PATH).read_text()
    statuses = re.search(r'^SuccessExitStatus=(.+)$', unit, re.MULTILINE)

    assert statuses is not None
    assert '6' in statuses.group(1).split()


def test_a_non_object_json_body_does_not_strand_the_display(
    viewer_wifi_state: None,
) -> None:
    """A 200 carrying a list parses fine, then AttributeErrors on .get."""
    import anthias_viewer as viewer

    viewer._wifi_setup_active = True

    with mock.patch(
        'anthias_viewer.requests.get',
        return_value=mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(return_value=['not', 'an', 'object']),
        ),
    ):
        viewer._poll_wifi_setup_once()

    assert not viewer._wifi_setup_active


def test_the_unit_and_compose_agree_on_the_container_name() -> None:
    """Compose would otherwise name it `anthias-anthias-wifi-connect-1`.

    The unit starts the portal with `docker start <name>`, so a
    mismatch means "No such container" and a portal that never
    appears. Invisible in testing until a device is genuinely
    offline, because every other path stops at the ExecCondition
    skip before `docker start` is reached — which is exactly how it
    got shipped into review.
    """
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    unit = (REPO_ROOT / UNIT_PATH).read_text()

    declared = re.search(
        r'^\s+container_name:\s*(\S+)$', compose, re.MULTILINE
    )

    assert declared is not None, (
        'anthias-wifi-connect has no container_name, so compose will '
        'generate one the systemd unit cannot address'
    )
    name = declared.group(1)
    assert f'docker start --attach {name}' in unit
    assert f'docker stop --timeout 20 {name}' in unit


def test_the_upgrade_script_precreates_the_named_container() -> None:
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    declared = re.search(
        r'^\s+container_name:\s*(\S+)$', compose, re.MULTILINE
    )
    assert declared is not None
    name = declared.group(1)
    body = (REPO_ROOT / 'bin/upgrade_containers.sh').read_text()

    # The pre-create addresses the compose SERVICE (profiles key off
    # service names); the unit addresses the CONTAINER. Both happen to
    # be the same string here, which is the point of pinning it.
    assert f'--no-start {name}' in body


# ---------------------------------------------------------------------------
# Portal branding
# ---------------------------------------------------------------------------
#
# The page the user opens on their phone is wifi-connect's, not ours. It
# follows Anthias's accent only because we hand it a stylesheet; nothing
# else connects the two, so the wiring is worth pinning.

BRAND_CSS_PATH = 'ansible/roles/network/files/wifi-connect-brand.css'


def test_the_portal_is_pointed_at_the_anthias_stylesheet() -> None:
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()

    declared = re.search(r'WIFI_CONNECT_BRAND_CSS=(\S+)', compose)

    assert declared is not None, 'the portal is not branded'
    # The path has to be inside the bind mount, or the portal exits at
    # startup with "could not read --brand-css".
    assert declared.group(1).startswith('/brand/')
    assert 'target: /brand' in compose


def test_ansible_installs_the_stylesheet_where_compose_mounts_it() -> None:
    tasks = (REPO_ROOT / 'ansible/roles/network/tasks/main.yml').read_text()
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()

    source = re.search(
        r'source: (/etc/anthias/\S+)\n\s+target: /brand', compose
    )

    assert source is not None
    assert f'dest: {source.group(1)}/' in tasks


def test_the_brand_stylesheet_overrides_roles_only() -> None:
    """Upstream's contract: roles are the seam, primitives are not.

    A rebrand that reaches past the roles into the --c-* primitives or the
    markup comes adrift the next time the portal's layout changes.
    --font-sans is a role in the same sense — upstream declares it beside
    the colour roles — so it is allowed by name rather than by pattern.
    """
    css = (REPO_ROOT / BRAND_CSS_PATH).read_text()
    declarations = re.findall(r'^\s*(--[a-z0-9-]+)\s*:', css, re.MULTILINE)

    assert declarations, 'no custom properties in the brand stylesheet'
    for name in declarations:
        assert name.startswith('--color-') or name == '--font-sans', (
            f'{name} is not a role token; overriding it couples the '
            f'rebrand to upstream internals'
        )


def test_the_brand_stylesheet_touches_no_component_classes() -> None:
    """The gradient is the one non-role rule, and it is element-level.

    `body` and `html` are the two selectors a portal layout change cannot
    rename. A rule keyed on .card or .btn is exactly the coupling the
    roles-only discipline exists to avoid, so it stays out even though
    nothing stops us writing one.
    """
    css = (REPO_ROOT / BRAND_CSS_PATH).read_text()
    # Strip comments first: the header discusses the very things this
    # forbids, and matching prose would fail on the explanation.
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    # Everything between a } and the next { is a selector. The file has no
    # nested at-rules, which a regex over the whole text would mangle.
    selectors = [
        chunk.rsplit('{', 1)[0].strip()
        for chunk in css.split('}')
        if '{' in chunk
    ]

    assert selectors, 'no rules in the brand stylesheet'
    assert set(selectors) <= {':root', 'body'}, (
        f'unexpected selector: {sorted(set(selectors) - {":root", "body"})}'
    )


def test_the_brand_stylesheet_uses_the_anthias_palette() -> None:
    """Not just the accent: the canvas is what makes it read as Anthias.

    The first version of this file set four accent tokens and left canvas,
    surface, text and fields on upstream's defaults, which produced a
    generic card UI with a yellow button on it.
    """
    css = (REPO_ROOT / BRAND_CSS_PATH).read_text().lower()
    palette = (
        (REPO_ROOT / 'src/anthias_server/app/static/src/css/palette.css')
        .read_text()
        .lower()
    )

    for token, value in (
        ('--color-canvas', '#270035'),  # plum-800
        ('--color-accent', '#ffe11a'),  # sun-400
        ('--color-on-accent', '#1f002a'),
        ('--color-fg', '#ffffff'),
    ):
        assert f'{token}: {value}' in css, f'{token} is not the Anthias value'
        if value not in ('#ffffff', '#1f002a'):
            assert value in palette, f'{value} is not in the Anthias palette'


def test_the_portal_carries_the_anthias_mark() -> None:
    """And it is the same file the splash screen uses, not a lookalike."""
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    installed = (
        REPO_ROOT / 'ansible/roles/network/files/wifi-connect-logo.svg'
    ).read_bytes()
    canonical = (
        REPO_ROOT / 'src/anthias_server/app/static/img/logo-full-splash.svg'
    ).read_bytes()

    assert re.search(r'WIFI_CONNECT_BRAND_LOGO=/brand/\S+\.svg', compose)
    assert installed == canonical, (
        'the portal logo has drifted from the splash logo'
    )


def test_the_theme_is_ours_to_pick() -> None:
    """A pinned theme is also what removes the portal's own switcher."""
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()

    assert 'WIFI_CONNECT_DEFAULT_THEME=dark' in compose


def test_branding_requires_the_release_that_implements_it() -> None:
    """Each piece needs the release that made it do anything.

    --brand-css landed in 2026.10.1 but could not reach the controls until
    2026.10.2 fixed the cascade, and --default-theme and --brand-logo are
    2026.10.2 as well. Earlier images accept the variables and ignore
    them, so the pin and the branding move together.

    2026.10.3 is the one the systemd unit depends on: before it, a portal
    that could not bind its port exited 6, which SuccessExitStatus lists,
    so a port conflict looked like an ordinary timeout.
    """
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    tag = re.search(
        r'ghcr\.io/screenly-labs/wifi-connect:(\d{4}\.\d{2}\.\d+)', compose
    )

    assert tag is not None
    major, minor, patch = (int(p) for p in tag.group(1).split('.'))
    assert (major, minor, patch) >= (2026, 10, 3)


# ---------------------------------------------------------------------------
# The ExecCondition gate
# ---------------------------------------------------------------------------
#
# bin/wifi_connect_needed.sh decides whether a device gets a captive
# portal raised at it. Getting that wrong is expensive in both
# directions: a false "raise" tears down a working network or replaces
# the content on an unattended screen, and a false "skip" leaves a
# first-boot device with no way to get online.
#
# It is shelled out to, with nmcli / curl / docker stubbed, because the
# logic lives in shell and reimplementing it here would test the
# reimplementation.

GATE = REPO_ROOT / 'bin' / 'wifi_connect_needed.sh'

# One device per line, as `nmcli -t -f DEVICE,TYPE,STATE[,CONNECTION]`
# prints it. The gate asks for a 4-field form and a 3-field form, and
# awk's -F: handles the short rows, so one fixture serves both.
NMCLI_WIFI_ASSOCIATED = (
    'wlan0:wifi:connected:factory-local\neth0:ethernet:unavailable:'
)
NMCLI_WIRED = (
    'wlan0:wifi:disconnected:\neth0:ethernet:connected:Wired connection 1'
)
NMCLI_PORTAL_ONLY = (
    'wlan0:wifi:connected:wifi-connect-portal\neth0:ethernet:unavailable:'
)
NMCLI_NOTHING = 'wlan0:wifi:disconnected:\neth0:ethernet:unavailable:'
NMCLI_SETTLING = (
    'wlan0:wifi:connecting:factory-local\neth0:ethernet:unavailable:'
)


def run_gate(
    tmp_path: Path,
    nmcli: str,
    *,
    online: bool = False,
    container: bool = True,
    state: str | None = None,
) -> tuple[int, str]:
    """Run the real gate with its three external commands stubbed."""
    import os
    import subprocess

    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    fixture = tmp_path / 'nmcli.out'
    fixture.write_text(nmcli + '\n')

    (bindir / 'nmcli').write_text(f'#!/bin/sh\ncat {fixture}\n')
    (bindir / 'curl').write_text(f'#!/bin/sh\nexit {0 if online else 7}\n')
    (bindir / 'docker').write_text(
        f'#!/bin/sh\nexit {0 if container else 1}\n'
    )
    for stub in ('nmcli', 'curl', 'docker'):
        (bindir / stub).chmod(0o755)

    runtime = tmp_path / 'run'
    runtime.mkdir(exist_ok=True)
    if state is not None:
        (runtime / 'raises').write_text(state)

    proc = subprocess.run(
        ['sh', str(GATE)],
        # A non-zero exit is the answer, not an error: 1 means "skip".
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            'PATH': f'{bindir}:{os.environ["PATH"]}',
            'RUNTIME_DIRECTORY': str(runtime),
        },
    )
    return proc.returncode, proc.stdout


# Exit 1 is "skip" (systemd's ExecCondition treats a clean non-zero as
# "do not run"); exit 0 raises the portal.
SKIP, RAISE = 1, 0


@pytest.mark.parametrize(
    ('name', 'nmcli', 'online', 'expected'),
    [
        ('wifi associated, no internet', NMCLI_WIFI_ASSOCIATED, False, SKIP),
        ('wired, no internet', NMCLI_WIRED, False, SKIP),
        ('nothing, but internet somehow', NMCLI_NOTHING, True, SKIP),
        ('only our own portal AP is up', NMCLI_PORTAL_ONLY, False, RAISE),
        ('nothing at all', NMCLI_NOTHING, False, RAISE),
    ],
)
def test_the_gate_decides_the_same_way_offline_as_online(
    tmp_path: Path, name: str, nmcli: str, online: bool, expected: int
) -> None:
    """An airgapped site must not look like a device that needs setup.

    Every skip here is reached without the internet probe mattering —
    which is the whole point. A device on an airgapped LAN, or
    associated to an airgapped SSID, is configured; it just cannot
    reach 1.1.1.1, and raising an access point at it would tear down
    the network it is managed over.
    """
    code, _ = run_gate(tmp_path, nmcli, online=online)

    assert code == expected, name


def test_a_missing_container_is_a_skip_not_a_failed_unit(
    tmp_path: Path,
) -> None:
    """The airgapped case: the image could never be pulled.

    upgrade_containers.sh pre-creates the container best-effort, so a
    device that cannot reach ghcr.io has a compose file naming a
    container that does not exist. Raising here would hand ExecStart a
    `docker start` that fails every five minutes forever.
    """
    code, out = run_gate(tmp_path, NMCLI_NOTHING, container=False)

    assert code == SKIP
    assert 'does not exist' in out


def test_the_portal_stops_being_raised_every_five_minutes(
    tmp_path: Path,
) -> None:
    """After a few goes, back off — the screen is showing setup, not content."""
    import time

    recent = f'3 {int(time.time())}'
    code, out = run_gate(tmp_path, NMCLI_NOTHING, state=recent)

    assert code == SKIP
    assert 'more minutes before' in out


def test_the_backoff_expires_rather_than_giving_up(tmp_path: Path) -> None:
    """A network can appear later, and nobody will be there to notice."""
    import time

    stale = f'9 {int(time.time()) - 7 * 60 * 60}'
    code, _ = run_gate(tmp_path, NMCLI_NOTHING, state=stale)

    assert code == RAISE


def test_going_online_forgets_the_attempt_history(tmp_path: Path) -> None:
    """Otherwise the next genuine outage starts already throttled."""
    runtime = tmp_path / 'run'
    runtime.mkdir()
    (runtime / 'raises').write_text('3 1')

    run_gate(tmp_path, NMCLI_WIRED)

    assert not (runtime / 'raises').exists()


def test_a_corrupt_state_file_does_not_wedge_the_gate(tmp_path: Path) -> None:
    """It lives in /run and nothing stops an operator poking at it."""
    code, _ = run_gate(tmp_path, NMCLI_NOTHING, state='not-a-number banana')

    assert code == RAISE


def test_a_settled_device_is_not_made_to_wait(tmp_path: Path) -> None:
    """The retry window is for a boot that beat DHCP, not for every tick.

    Sleeping it out regardless costs 25s on every timer tick of every
    offline device, and delays the portal by 25s on the first boot this
    all exists for.
    """
    import time

    start = time.monotonic()
    code, _ = run_gate(tmp_path, NMCLI_NOTHING)
    elapsed = time.monotonic() - start

    assert code == RAISE
    assert elapsed < 5, f'settled device waited {elapsed:.1f}s'


def test_a_device_still_settling_is_given_the_full_window(
    tmp_path: Path,
) -> None:
    """A radio mid-association must not be called "needs setup"."""
    import time

    start = time.monotonic()
    run_gate(tmp_path, NMCLI_SETTLING)
    elapsed = time.monotonic() - start

    assert elapsed >= 20, f'still-settling device only waited {elapsed:.1f}s'


def test_the_unit_keeps_state_across_runs_but_not_across_reboots() -> None:
    """The backoff counter is useless if systemd deletes it between runs.

    RuntimeDirectory is removed when the unit stops unless Preserve is
    set, and the unit stops after every single attempt — so without
    this pair the gate would read a fresh counter every time and retry
    at five-minute intervals forever, which is the behaviour the
    backoff exists to end.
    """
    unit = (
        REPO_ROOT
        / 'ansible/roles/network/templates/anthias-wifi-connect.service'
    ).read_text()

    assert 'RuntimeDirectory=anthias-wifi-connect' in unit
    assert 'RuntimeDirectoryPreserve=yes' in unit
    # /run, not /var/lib: a reboot is an operator asking for another go.
    assert 'StateDirectory=' not in unit


def test_a_bind_failure_is_not_counted_as_success() -> None:
    """Exit 7, which upstream split out in 2026.10.3, must stay off the list."""
    unit = (
        REPO_ROOT
        / 'ansible/roles/network/templates/anthias-wifi-connect.service'
    ).read_text()

    success = re.search(r'^SuccessExitStatus=(.*)$', unit, re.MULTILINE)

    assert success is not None
    codes = success.group(1).split()
    assert '6' in codes, 'a timeout is an ordinary ending'
    assert '7' not in codes, 'a portal that cannot bind must surface as failed'


def test_the_splash_screen_calls_it_a_passphrase() -> None:
    """WPA's own word, and the one on the back of a router.

    The setup network is WPA2/WPA3, so what the screen displays is a
    passphrase. Saying "password" sends someone looking for an account
    credential — and the portal they open on their phone says
    passphrase, so disagreeing with it is worse than either word alone.
    """
    template = (
        REPO_ROOT / 'src/anthias_server/app/templates/hotspot.html'
    ).read_text()

    assert '<span class="splash-cred__label">Passphrase</span>' in template
    assert 'Password' not in template


def test_the_setup_screen_does_not_reload_itself() -> None:
    """The viewer's re-navigation is what keeps the credentials current.

    A `meta http-equiv="refresh"` used to sit on this page to cover the
    case of a page left up from a previous portal run showing a
    passphrase that no longer opens anything. It is not needed: the
    portal stays down for minutes between runs, the viewer's poll
    notices within WIFI_SETUP_POLL_INTERVAL_S and switches the display
    away, and switching back re-navigates here with fresh credentials.

    Kept out because /hotspot is opened from phones as well as the
    device's own display, and reloading the page under someone reading
    a 14-character passphrase off it is exactly the disruption
    Web:MetaRefreshCheck exists to prevent.
    """
    template = (
        REPO_ROOT / 'src/anthias_server/app/templates/hotspot.html'
    ).read_text()

    assert not re.search(r'<meta[^>]*http-equiv=["\']refresh', template)


def test_the_splash_screen_calls_it_an_ssid() -> None:
    """ "Network" is what the thing is; "SSID" is what the phone calls it.

    The screen sits next to a phone showing a list of SSIDs, and the
    label is there to tell someone which row to tap.
    """
    template = (
        REPO_ROOT / 'src/anthias_server/app/templates/hotspot.html'
    ).read_text()

    assert '<span class="splash-cred__label">SSID</span>' in template
    assert '>Network<' not in template


def test_the_setup_ssid_is_a_prefix_not_the_whole_name() -> None:
    """Five screens in one room must not broadcast one name.

    wifi-connect appends four random characters from 2026.10.6 on. The
    pin and this expectation move together: an earlier image takes
    PORTAL_SSID literally and every device in the room collides again,
    silently.
    """
    compose = (REPO_ROOT / 'docker-compose.yml.tmpl').read_text()
    tag = re.search(
        r'ghcr\.io/screenly-labs/wifi-connect:(\d{4})\.(\d{2})\.(\d+)', compose
    )

    assert tag is not None
    assert tuple(int(g) for g in tag.groups()) >= (2026, 10, 6)
    # Not set to 0 anywhere: that would opt back into the collision.
    assert 'PORTAL_SSID_SUFFIX' not in compose
