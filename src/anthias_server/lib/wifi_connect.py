"""Reading the state of the wifi-connect setup portal.

The portal itself is Screenly-Labs/wifi-connect running as the
``anthias-wifi-connect`` container (see the compose templates). It
publishes what a host application needs through ``--state-file``: one
atomic JSON write on the shared ``resin-data`` volume, mounted at
``/data`` here, carrying the SSID actually on the air, the per-run
passphrase, the join payload and the portal's address.

**Its presence is the liveness signal.** wifi-connect writes it once a
run has committed to raising the access point and removes it when the
run ends, on every exit. That distinction is the whole point: the
``--qr-output`` files are written *before* the portal's own preflight
decides whether a portal is needed, so a device that turned out to be
online has them too, and their presence would put a setup screen on a
healthy device.

Two further signals, because a portal can stop without saying so.

``portal_is_up`` corroborates with something the kernel owns: the
portal runs with ``ipv4.method=manual``, so NetworkManager holds
``PORTAL_GATEWAY`` on the WiFi interface for as long as the access
point is up, and the server already resolves the host's addresses for
the splash page.

The file's **heartbeat** covers what that cannot. A portal killed with
``SIGKILL`` removes neither its state file nor its access point --
nothing tore anything down, so NetworkManager keeps holding the
gateway and the radio keeps broadcasting, and every signal outside the
file says a healthy portal is running. Measured on a Pi 5.
wifi-connect refreshes ``heartbeat_unix`` every
``heartbeat_interval_s`` while it runs, and one that has stopped
beating is a portal that has stopped running.

Anthias does not probe the portal's HTTP listener, and nothing here
should start: it is bound to the gateway address scoped to the
wireless device, specifically so that a setup portal cannot answer
over a customer's wired LAN. A packet from the Docker bridge arrives
on the wrong interface and is refused — measured on a Pi 5 as 200 from
the host and a connection failure from this container. That is why the
state file exists.
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Mirrors PORTAL_GATEWAY / PORTAL_LISTENING_PORT / WIFI_CONNECT_QR_OUTPUT
# / WIFI_CONNECT_STATE_FILE in the compose templates. Changing either
# side alone silently breaks detection, so they are asserted against
# each other in tests/test_wifi_connect.py.
# NOSONAR (S1313): wifi-connect's fixed setup-AP address, not a
# deployment endpoint; it only exists while the device is its own AP.
PORTAL_GATEWAY = '192.168.42.1'  # NOSONAR
PORTAL_PORT = 8000
QR_DIRECTORY = Path('/data/hotspot')

QR_SVG_FILENAME = 'wifi-connect-qr.svg'
STATE_FILENAME = 'state.json'

PORTAL_URL = f'http://{PORTAL_GATEWAY}:{PORTAL_PORT}/'  # NOSONAR (S5332)

# The schema this code was written against. Upstream bumps it only for a
# removed field or one that changes meaning, and promises that added
# fields will not, so a higher number is read rather than refused: a
# screen that shows the passphrase is worth more than one that is
# correct about a version.
STATE_SCHEMA = 1

# How many missed beats before a state file is a leftover rather than a
# live portal. The interval itself comes from the file, so upstream can
# change its cadence without this going wrong; two spare beats is slack
# for a loaded device, and still notices a dead portal inside a minute.
_HEARTBEAT_GRACE_BEATS = 3

# Used only when a state file predates the heartbeat or omits it.
_DEFAULT_HEARTBEAT_INTERVAL_S = 15

# Cap on the state file we will read. It is ~400 bytes; this exists so a
# truncated or substituted file cannot be parsed into memory unbounded.
_MAX_STATE_BYTES = 64 * 1024

_MAX_SVG_BYTES = 256 * 1024


class PortalState(NamedTuple):
    """One portal run, as wifi-connect reported it."""

    ssid: str
    passphrase: str
    portal_url: str


def read_portal_state(directory: Path | None = None) -> PortalState | None:
    """Read wifi-connect's state file.

    ``None`` when no portal run is live: either the file was never
    written (a device that has not been offline since the volume was
    created) or the run that wrote it has ended and removed it.

    Never raises. A missing file is the ordinary case, and a malformed
    one is worth a log line and a setup screen without credentials
    rather than a 500 on the only page a stranded device can show.
    """
    path = (directory or QR_DIRECTORY) / STATE_FILENAME

    try:
        if path.stat().st_size > _MAX_STATE_BYTES:
            logger.warning(
                'Ignoring %s: %s bytes is far larger than a portal state file.',
                path,
                path.stat().st_size,
            )
            return None
        document = json.loads(path.read_text(encoding='utf-8'))
    except OSError:
        return None
    except ValueError:
        logger.warning('Could not parse %s as JSON.', path, exc_info=True)
        return None

    if not isinstance(document, dict):
        logger.warning('%s is not a JSON object.', path)
        return None

    # The field exists so that a partial or substituted file fails
    # closed: parsing is not the same as a portal being up.
    if document.get('portal_active') is not True:
        return None

    ssid = document.get('ssid')
    if not isinstance(ssid, str) or not ssid:
        logger.warning('%s carries no SSID.', path)
        return None

    # Null on an open access point, where there is nothing to show.
    passphrase = document.get('passphrase')
    if not isinstance(passphrase, str):
        passphrase = ''

    url = document.get('portal_url')
    if not isinstance(url, str) or not url:
        url = PORTAL_URL

    if _heartbeat_is_stale(document):
        logger.warning(
            'Ignoring %s: the portal stopped refreshing its heartbeat, so '
            'the run that wrote it is gone.',
            path,
        )
        return None

    return PortalState(ssid=ssid, passphrase=passphrase, portal_url=url)


def _heartbeat_is_stale(document: dict[str, object]) -> bool:
    """Has the portal that wrote this file stopped running?

    A portal killed hard enough cannot remove its own state file, and
    on that path NetworkManager is left holding the access point -- so
    the radio, the gateway address and the file all still say a portal
    is up while nothing serves the page. The heartbeat is the only
    thing that separates the two.

    A file with no heartbeat at all is trusted: it was written by a
    wifi-connect older than the field, where presence was the whole
    contract, and refusing it would blank the setup screen on a device
    whose portal is running perfectly well.
    """
    beat = document.get('heartbeat_unix')
    if not isinstance(beat, int):
        return False

    interval = document.get('heartbeat_interval_s')
    if not isinstance(interval, int) or interval <= 0:
        interval = _DEFAULT_HEARTBEAT_INTERVAL_S

    # A clock that jumped forward (NTP landing on a device that booted
    # with no RTC -- every Pi) makes a live beat look ancient. Nothing
    # here can tell that from a dead portal, and the address check is
    # the backstop for the one tick it takes the next beat to land.
    return time.time() - beat > interval * _HEARTBEAT_GRACE_BEATS


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
