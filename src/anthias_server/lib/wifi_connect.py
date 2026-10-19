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
  access point comes down. That makes "is this address on the host"
  the one liveness signal that cannot go stale, and the server already
  resolves the host's addresses for the splash page (via host_agent on
  bare metal, via the supervisor on balena) — so no new plumbing is
  needed to see it.

Both are needed. The QR files are written at the *start* of every run,
including the runs that exit a second later because the device turned
out to be online, so their presence alone would show a setup screen on
a perfectly healthy device.
"""

import logging
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Mirrors PORTAL_GATEWAY / PORTAL_LISTENING_PORT / WIFI_CONNECT_QR_OUTPUT
# in the compose templates. Changing either side alone silently breaks
# detection, so they are asserted against each other in
# tests/test_wifi_connect.py.
PORTAL_GATEWAY = '192.168.42.1'
PORTAL_PORT = 8000
QR_DIRECTORY = Path('/data/hotspot')

QR_SVG_FILENAME = 'wifi-connect-qr.svg'
QR_PAYLOAD_FILENAME = 'wifi-connect-qr.txt'

PORTAL_URL = f'http://{PORTAL_GATEWAY}:{PORTAL_PORT}/'  # NOSONAR (S5332)

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


def portal_is_up(node_ip: str) -> bool:
    """Is the setup access point live right now?

    Takes the raw whitespace-separated address string host_agent and
    the balena supervisor both publish, and looks for the access
    point's own gateway address. NetworkManager adds it when the
    access point goes up and removes it when it comes down, so this
    follows the portal exactly with no timeout to tune.
    """
    return PORTAL_GATEWAY in node_ip.split()


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
