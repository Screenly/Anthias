"""Reading the state of the wifi-connect setup portal.

The portal itself is Screenly-Labs/wifi-connect running as the
``anthias-wifi-connect`` container (see the compose templates). It has
no API this container could call and no hook to fire, so Anthias reads
two artefacts it leaves behind:

* **The join QR.** ``--qr-output`` writes ``wifi-connect-qr.svg`` and
  ``wifi-connect-qr.txt`` into a directory on the shared ``resin-data``
  volume, which is mounted at ``/data`` here. The ``.txt`` is the raw
  ``WIFI:...`` payload the SVG encodes, so it — not a log line — is
  where the SSID and the per-run passphrase come from.

* **The access-point address.** The portal runs with
  ``ipv4.method=manual``, so while it is up NetworkManager holds
  ``PORTAL_GATEWAY`` on the WiFi interface and drops it the moment the
  access point comes down. The server already resolves the host's
  addresses for the splash page (via host_agent on bare metal, via the
  supervisor on balena), so seeing that costs no new plumbing.

Neither is sufficient alone, which is why ``wifi_setup_state`` uses
both plus a probe:

* The QR files are written at the *start* of every run, including the
  runs that exit a second later because the device turned out to be
  online — so their presence would show a setup screen on a healthy
  device.

* The address list is a *cache*. It is only as current as whatever
  last refreshed it, and on the Pi 5 testbed a host_agent that had
  stopped froze it with the access point's address still in it: the
  setup screen stayed on the display for a portal that had been down
  for minutes. ``portal_is_serving`` is the liveness check that
  closes that hole.
"""

import base64
import logging
import re
from pathlib import Path
from typing import NamedTuple

import redis

logger = logging.getLogger(__name__)

# Mirrors PORTAL_GATEWAY / PORTAL_LISTENING_PORT / WIFI_CONNECT_QR_OUTPUT
# in the compose templates. Changing either side alone silently breaks
# detection, so they are asserted against each other in
# tests/test_wifi_connect.py.
# NOSONAR (S1313): wifi-connect's fixed setup-AP address, not a
# deployment endpoint; it only exists while the device is its own AP.
PORTAL_GATEWAY = '192.168.42.1'  # NOSONAR
PORTAL_PORT = 8000
QR_DIRECTORY = Path('/data/hotspot')

QR_SVG_FILENAME = 'wifi-connect-qr.svg'
QR_PAYLOAD_FILENAME = 'wifi-connect-qr.txt'

PORTAL_URL = f'http://{PORTAL_GATEWAY}:{PORTAL_PORT}/'  # NOSONAR (S5332)

# wifi-connect's RFC 8908 captive-portal API, which only answers while
# the portal is actually serving. Used as the liveness confirmation
# below. Short timeout: when the access point is down this address is
# not on the host at all, so the request has nowhere to go and we must
# not hold a request worker waiting to find that out.
# Published by anthias_host_agent, which can actually reach the portal.
# Keep in step with PORTAL_SERVING_KEY there.
PORTAL_SERVING_KEY = 'wifi_setup_portal_serving'

# Cap on the SVG we will inline into the page. The real file is ~4 KB;
# this only exists so a truncated or substituted file can't be read
# into a response body unbounded.
_MAX_SVG_BYTES = 256 * 1024


class PortalCredentials(NamedTuple):
    """The SSID and passphrase of the access point currently offered."""

    ssid: str
    passphrase: str


def _unescape_field(value: str) -> str:
    r"""Undo the ``\``-escaping the WIFI: payload applies.

    The payload escapes ``\``, ``;``, ``,``, ``:`` and ``"``. An SSID
    containing any of them round-trips through here; everything else is
    returned unchanged.
    """
    return re.sub(r'\\(.)', r'\1', value)


def _split_payload_fields(body: str) -> list[str]:
    """Split a ``WIFI:`` body on unescaped semicolons."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False

    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == '\\':
            current.append(char)
            escaped = True
        elif char == ';':
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)

    if current:
        fields.append(''.join(current))

    return fields


def parse_qr_payload(payload: str) -> PortalCredentials | None:
    """Pull the SSID and passphrase out of a ``WIFI:`` QR payload.

    wifi-connect emits ``WIFI:T:WPA;S:<ssid>;P:<passphrase>;H:false;;``.
    Field order is not relied on here — the format does not guarantee
    one, and a future upstream reordering should not blank the setup
    screen. Returns ``None`` for anything that isn't a WiFi payload or
    is missing an SSID.
    """
    payload = payload.strip()
    if not payload.startswith('WIFI:'):
        return None

    ssid = None
    passphrase = ''
    for field in _split_payload_fields(payload[len('WIFI:') :]):
        key, separator, value = field.partition(':')
        if not separator:
            continue
        if key == 'S':
            ssid = _unescape_field(value)
        elif key == 'P':
            passphrase = _unescape_field(value)

    if not ssid:
        return None

    return PortalCredentials(ssid=ssid, passphrase=passphrase)


def read_portal_credentials(
    directory: Path | None = None,
) -> PortalCredentials | None:
    """Read the credentials of the access point from the QR payload.

    ``None`` when the portal has not written one — a device that has
    never been offline since the volume was created, or a run that died
    before it got that far. Never raises: a setup screen that renders
    without the passphrase is worth more than a 500.
    """
    path = (directory or QR_DIRECTORY) / QR_PAYLOAD_FILENAME
    try:
        payload = path.read_text(encoding='utf-8')
    except OSError:
        return None

    return parse_qr_payload(payload)


def read_qr_svg(directory: Path | None = None) -> str | None:
    """Read the rendered join QR, for inlining into the setup page.

    Inlined rather than served as a file so the page is one request:
    the viewer renders it on a device that by definition has no network
    worth waiting on, and a second round trip to the same container is
    a second chance to show a broken-image icon.
    """
    path = (directory or QR_DIRECTORY) / QR_SVG_FILENAME
    try:
        if path.stat().st_size > _MAX_SVG_BYTES:
            logger.warning(
                'Ignoring %s: %s bytes is far larger than a join QR.',
                path,
                path.stat().st_size,
            )
            return None
        return path.read_text(encoding='utf-8')
    except OSError:
        return None


def read_qr_data_uri(directory: Path | None = None) -> str | None:
    """The join QR as a ``data:`` URI, for an ``<img src>``.

    Still one request, but the SVG is drawn as an image rather than
    parsed into the page, so anything other than paths and rects in the
    file (a script, a handler, a link) has nothing to run in.
    """
    svg = read_qr_svg(directory)
    if svg is None:
        return None
    encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'


def portal_is_up(node_ip: str) -> bool:
    """Is the setup access point live right now?

    Takes the raw whitespace-separated address string host_agent and
    the balena supervisor both publish, and looks for the access
    point's own gateway address. NetworkManager adds it when the
    access point goes up and removes it when it comes down, so this
    follows the portal exactly with no timeout to tune.
    """
    return PORTAL_GATEWAY in node_ip.split()


def portal_is_serving(rdb: 'redis.Redis') -> bool:
    """Is the portal answering on the gateway right now?

    ``portal_is_up`` reads a *cached* address list — host_agent writes
    it on request, the balena supervisor answers from its own state —
    and a cache has no obligation to be current. On the Pi 5 testbed a
    stopped host_agent froze that list with the access point's address
    still in it, and the setup screen stayed on the display
    indefinitely for a portal that had been down for minutes. Anything
    that stops the list being refreshed does the same.

    So the address list is the cheap gate and this is the authority.
    But the question cannot be asked from in here: wifi-connect binds
    its listener to the access point's address scoped to the wireless
    device (`192.168.42.1%wlan0`), so that it can never answer on a
    customer's ethernet LAN — and a packet from the Docker bridge
    arrives on the wrong interface and is refused. This container gets
    a connection failure every time, however healthy the portal is,
    which made the first version of this function a permanent "no" and
    the setup screen unreachable.

    So host_agent runs the probe out on the host where the wireless
    device is, and publishes the answer alongside the address list it
    is already refreshing for us. Absent key reads as not serving,
    which fails towards playing content.
    """
    try:
        return rdb.get(PORTAL_SERVING_KEY) == 'true'
    except redis.RedisError:
        # Redis is how every other host fact arrives; if it is down the
        # device has larger problems and the honest answer is "no".
        logging.getLogger(__name__).warning(
            'Could not read the portal-serving fact from Redis', exc_info=True
        )
        return False


def strip_portal_gateway(node_ip: str) -> str:
    """Drop the access point's own address from a host address list.

    While the portal is up, ``192.168.42.1`` is a real address on this
    host and every address resolver reports it. It is not an address
    anyone should be told to manage the device on, though: it exists
    only for the duration of the setup flow and disappears the moment
    the device joins a network. The splash page's "open this on your
    phone" list is strictly worse for carrying it.
    """
    return ' '.join(
        token for token in node_ip.split() if token != PORTAL_GATEWAY
    )
