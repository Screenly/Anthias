"""HDMI-CEC support built on the kernel CEC uABI (``/dev/cec*``).

Anthias used to reach CEC through libcec (the ``cec`` PyPI wheel and
``cec-client``). That was replaced because libcec cannot answer the two
questions this feature actually needs:

  * **Which adapter?** libcec 7.0.0 enumerates the kernel adapter as com
    port ``'Linux'`` and treats a positional ``/dev/cecN`` as a
    Pulse-Eight *serial* port. Passing ``/dev/cec1`` therefore does not
    select the second HDMI port — measured on the Pi 5 testbed it fails
    with ``error opening serial port '/dev/cec1': Couldn't lock the
    serial port`` after 11.07s of retries, having sent nothing. With
    autodetect it works but picks whichever adapter it finds first, so
    a board with two HDMI outputs is a coin toss.
  * **Is there anything there?** libcec conflates "no adapter", "no
    HDMI link" and "display present but not CEC-capable" into a single
    failure, which is what made GH #3267 unactionable.

The kernel API answers both directly and cheaply. Measured across the
whole testbed fleet (Pi 2, Pi 3 A+, Pi 3 B+, Pi 4, Pi 5, Rock Pi 4,
x86), from inside the unprivileged server container:

  * ``CEC_ADAP_G_CAPS`` / ``G_PHYS_ADDR`` / ``G_CONNECTOR_INFO`` all
    answer in **0.0-0.1 ms**, versus 3.2s (best case) to 11s (worst)
    for a libcec round trip.
  * A physical address of ``f.f.f.f`` means no CEC link, and a valid
    one (e.g. ``1.0.0.0``) means the display's EDID advertised CEC.
    That is a real, instant answer to "is this port worth talking to".
  * ``G_CONNECTOR_INFO`` reports the DRM card and connector each
    adapter belongs to, so adapter-to-HDMI-port mapping is discovered
    rather than hardcoded.

There is deliberately **no adapter selection**. A device can have more
than one display attached (two HDMI ports on Pi 4/5, more on x86), and
picking one would leave the other powered on. Every operation fans out
across all adapters with a live link; a dead adapter NACKs in ~11 ms,
so the fan-out is cheap.

The ioctl encodings below were verified against real hardware rather
than transcribed from a header — see ``_ioc``.
"""

import fcntl
import glob
import logging
import os
import struct
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# uABI constants (linux/cec.h)
# ---------------------------------------------------------------------


def _ioc(direction: int, nr: int, size: int) -> int:
    """Encode an ioctl request number for the CEC device class.

    Layout is the asm-generic one every target arch uses:
    ``dir<<30 | size<<16 | type<<8 | nr``, with type ``'a'`` (0x61).
    Directions are 1=write, 2=read, 3=read/write.

    These were confirmed empirically on a Pi 5 by scanning the request
    space and recording which numbers the ``vc4_hdmi`` driver accepted
    (anything wrong returns ``ENOTTY``), because an off-by-two in
    ``CEC_ADAP_G_CONNECTOR_INFO``'s ``nr`` silently invalidated an
    earlier probe.
    """
    return (direction << 30) | (size << 16) | (0x61 << 8) | nr


_SZ_CAPS = 76  # struct cec_caps
_SZ_LOG_ADDRS = 92  # struct cec_log_addrs
_SZ_MSG = 56  # struct cec_msg
_SZ_CONNECTOR_INFO = 68  # struct cec_connector_info

CEC_ADAP_G_CAPS = _ioc(3, 0, _SZ_CAPS)
CEC_ADAP_G_PHYS_ADDR = _ioc(2, 1, 2)
CEC_ADAP_G_LOG_ADDRS = _ioc(2, 3, _SZ_LOG_ADDRS)
CEC_ADAP_S_LOG_ADDRS = _ioc(3, 4, _SZ_LOG_ADDRS)
CEC_TRANSMIT = _ioc(3, 5, _SZ_MSG)
CEC_ADAP_G_CONNECTOR_INFO = _ioc(2, 10, _SZ_CONNECTOR_INFO)

# struct layouts. '<' disables native alignment, so trailing padding is
# spelled out ('x') to keep the sizes matching the kernel's.
_FMT_CAPS = '<32s32sIII'
_FMT_LOG_ADDRS = '<4sHBBII15s4s4s4s48sx'
_FMT_MSG = '<QQIIII16s7Bx'
_FMT_CONNECTOR_INFO = '<III'

# Capability bits we care about.
CEC_CAP_LOG_ADDRS = 0x002
CEC_CAP_TRANSMIT = 0x004
CEC_CAP_CONNECTOR_INFO = 0x100

# An invalid physical address — the adapter exists but no CEC-capable
# sink is reachable through it (nothing plugged in, or the attached
# display's EDID carries no CEC block).
CEC_PHYS_ADDR_INVALID = 0xFFFF

# Logical addresses.
CEC_LOG_ADDR_TV = 0
CEC_LOG_ADDR_UNREGISTERED = 15

# Logical-address types / primary device types. We present as a
# playback device: that is what a signage player is, and TVs accept
# power commands from it.
#
# These three come from *different* enumerations in linux/cec.h and do
# not share numbering — a trap worth spelling out, because an earlier
# version of this file used the audio-system values for two of them.
# The kernel picks the candidate logical address from ``log_addr_type``
# (``type2addr[]`` in cec-adap.c), so claiming type 4 took LA 5 (Audio
# System) instead of LA 4 (Playback Device 1) and advertised an audio
# system in ``all_device_types``. A TV that sees an audio system appear
# on the bus commonly enables System Audio Control / ARC and mutes its
# own speakers — which would silence a signage player every time the
# 5-minutely power probe ran.
CEC_LOG_ADDR_TYPE_PLAYBACK = 3  # NOT 4 — that is AUDIOSYSTEM
CEC_OP_PRIM_DEVTYPE_PLAYBACK = 4  # different enum; 4 is correct here
CEC_OP_ALL_DEVTYPE_PLAYBACK = 0x10  # NOT 0x08 — that is AUDIOSYSTEM
CEC_OP_CEC_VERSION_1_4 = 5
CEC_VENDOR_ID_NONE = 0xFFFFFFFF

# Opcodes.
CEC_MSG_IMAGE_VIEW_ON = 0x04
CEC_MSG_STANDBY = 0x36
CEC_MSG_GIVE_DEVICE_POWER_STATUS = 0x8F
CEC_MSG_REPORT_POWER_STATUS = 0x90

# Power-status operands carried by REPORT_POWER_STATUS.
CEC_OP_POWER_STATUS_ON = 0x00
CEC_OP_POWER_STATUS_STANDBY = 0x01
CEC_OP_POWER_STATUS_TO_ON = 0x02
CEC_OP_POWER_STATUS_TO_STANDBY = 0x03

# tx_status bits. NACK/MAX_RETRIES is the ordinary answer from a plain
# desktop monitor: the adapter transmitted fine, nothing acknowledged.
CEC_TX_STATUS_OK = 0x01
CEC_TX_STATUS_NACK = 0x04
CEC_TX_STATUS_MAX_RETRIES = 0x20

CEC_RX_STATUS_OK = 0x01

# How long the kernel waits for a reply to a query, in milliseconds.
# The Pi 5 testbed answered a NACKed GIVE_DEVICE_POWER_STATUS in 0.58s
# with the default 1s; a real TV replies far faster.
_REPLY_TIMEOUT_MS = 1000

# Wall-clock guard for a whole CEC operation. Every ioctl here is
# bounded by the driver, but a blocking ioctl cannot be interrupted
# in-process, and a wedged celery worker is exactly the Sentry class
# (ANTHIAS-A/9/B/31) this feature has repeatedly caused. Operations run
# on a daemon thread and the caller gives up after this long.
_OPERATION_TIMEOUT_S = 5.0

# Time budget for the logical-address claim to complete. The kernel
# claims asynchronously; the Pi 5 settled in 0.16s.
_CLAIM_TIMEOUT_S = 2.0
_CLAIM_POLL_S = 0.02

_OSD_NAME = b'Anthias'


class PowerStatus(StrEnum):
    """Outcome of asking the attached display(s) about power state."""

    ON = 'on'
    STANDBY = 'standby'
    #: Transitioning between the two — reported by some TVs.
    TRANSITIONING = 'transitioning'
    #: The adapter transmitted but nothing acknowledged. This is the
    #: normal answer for a plain monitor with no CEC support, which is
    #: a large share of signage installs — not an error.
    NO_PEER = 'no-peer'
    #: The adapter exists but has no CEC link at all (phys addr
    #: f.f.f.f): nothing plugged in, or the sink advertises no CEC.
    NO_LINK = 'no-link'
    #: No CEC adapter on this device at all (e.g. x86 without a dongle).
    NO_ADAPTER = 'no-adapter'
    #: A reply arrived but was not one we understand.
    UNKNOWN = 'unknown'
    #: The query could not be completed — the node would not open, an
    #: ioctl failed, or we could not get on the bus. Deliberately
    #: distinct from ``NO_PEER``: "we could not ask" and "we asked and
    #: nothing answered" look identical to a user but mean opposite
    #: things, and collapsing them is the exact conflation that made GH
    #: #3267 unactionable in the libcec implementation.
    ERROR = 'error'


class CecError(Exception):
    """A CEC device could not be opened or driven."""


class CecBusyError(CecError):
    """Another process holds the CEC bus, so nothing was transmitted.

    Distinct from a failed transmit: callers that latch state on the
    outcome must retry rather than record the command as delivered.
    """


#: Everything a CEC operation can fail with, for callers to catch.
#:
#: ``TimeoutError`` is deliberately **absent**: PEP 3151 made it a
#: subclass of ``OSError``, so naming both is redundant. That is subtle
#: enough to be worth stating once here rather than re-deriving it at
#: each call site — and ``_run_bounded`` does raise a plain
#: ``TimeoutError``, which this tuple therefore already covers.
CEC_ERRORS = (OSError, CecError)


def _run_bounded[T](fn: Callable[[], T], timeout: float) -> T:
    """Run ``fn`` on a daemon thread, giving up after ``timeout``.

    A blocking ioctl is not interruptible from Python, so a genuinely
    wedged driver cannot be cancelled — only abandoned. The thread is a
    daemon so it cannot hold up interpreter exit, and it owns its own
    file descriptor, so abandoning it leaks at most one fd rather than
    corrupting the caller's state.

    Raises ``TimeoutError`` if ``fn`` did not finish in time, and
    re-raises whatever ``fn`` raised otherwise.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box['value'] = fn()
        except BaseException as exc:  # NOSONAR - re-raised on the caller
            # Deliberately broad and deliberately not re-raised *here*:
            # an exception raised on a worker thread would otherwise be
            # printed by the threading module and lost, leaving the
            # caller to see only `None`. It is stashed and re-raised on
            # the calling thread a few lines below, which preserves the
            # normal propagation Sonar is asking for — it just cannot
            # see across the thread boundary.
            box['error'] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f'CEC operation exceeded {timeout:.1f}s')
    if 'error' in box:
        raise box['error']
    return cast(T, box.get('value'))


@dataclass(frozen=True)
class CecAdapter:
    """One CEC adapter, i.e. one ``/dev/cecN`` node.

    On a Raspberry Pi 4 or 5 there is one per HDMI output
    (``vc4-hdmi-0`` / ``vc4-hdmi-1``); the Pi 2/3 and the Rock Pi 4
    expose a single one. x86 exposes none without an add-on adapter —
    DisplayPort has no CEC channel and the Intel HDMI outputs do not
    expose one either (verified on the x86 testbed: 3 HDMI + 3 DP
    connectors, zero ``/dev/cec*``).
    """

    node: str
    driver: str
    name: str
    capabilities: int
    physical_address: int
    drm_card: int | None
    drm_connector_id: int | None

    @property
    def has_link(self) -> bool:
        """Whether a CEC-capable sink is reachable through this port.

        Derived from the physical address, which the driver takes from
        the attached display's EDID. This is the cheap, honest gate the
        old ``os.path.exists('/dev/cec0')`` check could never be: it
        answers in microseconds and reflects what is actually plugged
        in. It still does not promise the sink will *answer* — that
        depends on the peer and is only knowable by asking.
        """
        return self.physical_address != CEC_PHYS_ADDR_INVALID

    @property
    def can_transmit(self) -> bool:
        return bool(self.capabilities & CEC_CAP_TRANSMIT)

    @property
    def physical_address_str(self) -> str:
        pa = self.physical_address
        return (
            f'{(pa >> 12) & 0xF:x}.{(pa >> 8) & 0xF:x}.'
            f'{(pa >> 4) & 0xF:x}.{pa & 0xF:x}'
        )

    # -- enumeration --------------------------------------------------

    @classmethod
    def enumerate(cls) -> list['CecAdapter']:
        """Every CEC adapter on this device, in node order.

        Adapters that cannot be opened or queried are skipped rather
        than raising: a partially broken adapter should not stop us
        driving the working ones.
        """
        adapters = []
        for node in sorted(glob.glob('/dev/cec*')):
            try:
                adapters.append(cls._probe(node))
            except (OSError, CecError):
                continue
        return adapters

    @classmethod
    def live(cls) -> list['CecAdapter']:
        """Adapters with a CEC link, i.e. the ones worth transmitting to."""
        return [
            adapter
            for adapter in cls.enumerate()
            if adapter.has_link and adapter.can_transmit
        ]

    @classmethod
    def _probe(cls, node: str) -> 'CecAdapter':
        with _open_node(node) as fd:
            caps = bytearray(_SZ_CAPS)
            fcntl.ioctl(fd, CEC_ADAP_G_CAPS, caps)
            driver, name, _avail, capabilities, _ver = struct.unpack(
                _FMT_CAPS, bytes(caps)
            )

            pa_buf = bytearray(2)
            fcntl.ioctl(fd, CEC_ADAP_G_PHYS_ADDR, pa_buf)
            (physical_address,) = struct.unpack('<H', bytes(pa_buf))

            card: int | None = None
            connector: int | None = None
            if capabilities & CEC_CAP_CONNECTOR_INFO:
                # Not fatal if it fails — the connector mapping is
                # diagnostic detail, not something power control needs.
                try:
                    ci = bytearray(_SZ_CONNECTOR_INFO)
                    fcntl.ioctl(fd, CEC_ADAP_G_CONNECTOR_INFO, ci)
                    ci_type, card_no, connector_id = struct.unpack(
                        _FMT_CONNECTOR_INFO, bytes(ci[:12])
                    )
                    if ci_type:
                        card, connector = card_no, connector_id
                except OSError:
                    pass

        return cls(
            node=node,
            driver=_cstr(driver),
            name=_cstr(name),
            capabilities=capabilities,
            physical_address=physical_address,
            drm_card=card,
            drm_connector_id=connector,
        )

    # -- operations ---------------------------------------------------

    def power_status(self) -> PowerStatus:
        """Ask the display on this port whether it is on."""
        if not self.has_link:
            return PowerStatus.NO_LINK
        return _run_bounded(self._power_status, _OPERATION_TIMEOUT_S)

    def set_power(self, on: bool) -> bool:
        """Turn the display on this port on or off.

        Returns whether the message was acknowledged. A ``False`` here
        means nothing on the bus answered, which for a plain monitor is
        expected rather than a fault.
        """
        if not self.has_link:
            return False
        return _run_bounded(lambda: self._set_power(on), _OPERATION_TIMEOUT_S)

    def _power_status(self) -> PowerStatus:
        with _open_node(self.node) as fd:
            initiator = _claim_logical_address(fd)
            try:
                tx_status, rx_status, reply = _transmit(
                    fd,
                    initiator,
                    CEC_LOG_ADDR_TV,
                    CEC_MSG_GIVE_DEVICE_POWER_STATUS,
                    reply=CEC_MSG_REPORT_POWER_STATUS,
                )
            finally:
                _release_logical_address(fd)

        if not tx_status & CEC_TX_STATUS_OK:
            return PowerStatus.NO_PEER
        if not rx_status & CEC_RX_STATUS_OK or len(reply) < 3:
            return PowerStatus.NO_PEER
        return {
            CEC_OP_POWER_STATUS_ON: PowerStatus.ON,
            CEC_OP_POWER_STATUS_STANDBY: PowerStatus.STANDBY,
            CEC_OP_POWER_STATUS_TO_ON: PowerStatus.TRANSITIONING,
            CEC_OP_POWER_STATUS_TO_STANDBY: PowerStatus.TRANSITIONING,
        }.get(reply[2], PowerStatus.UNKNOWN)

    def _set_power(self, on: bool) -> bool:
        opcode = CEC_MSG_IMAGE_VIEW_ON if on else CEC_MSG_STANDBY
        with _open_node(self.node) as fd:
            initiator = _claim_logical_address(fd)
            try:
                tx_status, _rx, _reply = _transmit(
                    fd, initiator, CEC_LOG_ADDR_TV, opcode
                )
            finally:
                _release_logical_address(fd)
        return bool(tx_status & CEC_TX_STATUS_OK)


# ---------------------------------------------------------------------
# Fan-out helpers — every operation covers every live adapter.
# ---------------------------------------------------------------------


#: Serialises CEC access within this process.
#:
#: A plain in-process lock is sufficient because exactly one process on
#: the device drives CEC: the viewer. It is the only container that can
#: reach ``/dev/cec*`` on every board and every deployment (it is
#: ``privileged: true`` in all three compose templates, including both
#: balena ones), so anthias-server and anthias-celery ask it over the
#: Redis command bus instead of opening the devices themselves.
#:
#: The lock still matters within the viewer: ``_claim_logical_address``
#: has to clear the adapter before configuring it (the kernel returns
#: EBUSY otherwise), and a clear issued on one file descriptor would
#: unconfigure an operation in flight on another.
_bus_mutex = threading.Lock()

#: Deliberately short. The viewer dispatches commands sequentially from
#: a single subscriber loop, so in practice this lock is never
#: contended; it exists only so that if that invariant ever changes the
#: second caller bails out quickly instead of blocking. Keeping it small
#: is what lets the worst-case operation time stay inside the reply
#: budget in ``cec_client`` — see MAX_OPERATION_S below.
_LOCK_WAIT_S = 2.0

#: The most adapters any supported board exposes (one per HDMI output;
#: Pi 4 and Pi 5 have two).
_MAX_ADAPTERS = 2

#: Worst-case wall clock for one fan-out, exported so ``cec_client`` can
#: size its reply timeouts from it rather than from a guessed constant.
#: A client that gives up while the viewer is still working would report
#: a fault that is not one.
MAX_OPERATION_S = _LOCK_WAIT_S + _MAX_ADAPTERS * _OPERATION_TIMEOUT_S


@contextmanager
def _bus_lock() -> Iterator[bool]:
    """Serialise CEC access.

    Yields ``False`` when the lock could not be taken in time; callers
    must treat that as "did not run" rather than "failed", so the caller
    retries instead of reporting a fault or latching state.
    """
    acquired = _bus_mutex.acquire(timeout=_LOCK_WAIT_S)
    if not acquired:
        logger.warning('CEC bus busy; skipping this operation')
        yield False
        return
    try:
        yield True
    finally:
        _bus_mutex.release()


def available() -> bool:
    """Whether this device has any CEC adapter at all.

    Note this is deliberately *not* "CEC will work here". No device-node
    probe can answer that, because it depends on the peer display (GH
    #3267). It answers "is there hardware worth showing controls for".
    """
    return bool(CecAdapter.enumerate())


def power_status() -> PowerStatus:
    """Aggregate power state across every live adapter.

    With more than one display attached the states can disagree, so the
    aggregate is deliberately conservative: ``ON`` only if every display
    that answered is on, ``STANDBY`` only if every one is in standby,
    and ``UNKNOWN`` when they disagree.
    """
    adapters = CecAdapter.enumerate()
    if not adapters:
        return PowerStatus.NO_ADAPTER

    live = [a for a in adapters if a.has_link and a.can_transmit]
    if not live:
        return PowerStatus.NO_LINK

    results = []
    with _bus_lock() as locked:
        if not locked:
            # Another process holds the bus. "We could not ask" — not a
            # fault, and not a claim about the display's state.
            return PowerStatus.ERROR
        for adapter in live:
            try:
                results.append(adapter.power_status())
            except CEC_ERRORS as exc:
                # Logged rather than silently folded in: a failure here
                # used to be indistinguishable from a plain monitor not
                # answering, hiding real faults behind a benign status.
                logger.warning(
                    'CEC power query failed on %s: %s', adapter.node, exc
                )
                results.append(PowerStatus.ERROR)

    real_states = (
        PowerStatus.ON,
        PowerStatus.STANDBY,
        PowerStatus.TRANSITIONING,
    )
    answered = [r for r in results if r in real_states]
    if not answered:
        # Nothing usable came back. Distinguish "we could not ask" from
        # "we asked and nothing answered" — only the former is a fault.
        if any(r == PowerStatus.ERROR for r in results):
            return PowerStatus.ERROR
        return PowerStatus.NO_PEER
    if all(r == PowerStatus.ON for r in answered):
        return PowerStatus.ON
    if all(r == PowerStatus.STANDBY for r in answered):
        return PowerStatus.STANDBY
    # Displays disagree, or one is mid-transition.
    return PowerStatus.UNKNOWN


def set_power(on: bool) -> tuple[int, int]:
    """Send an on/standby to every live adapter.

    Returns ``(acknowledged, attempted)``. Fanning out rather than
    picking one adapter is the whole point: a device with two monitors
    attached must not leave the second one lit.

    Raises ``CecError`` if the bus is held by another process, so a
    caller cannot mistake "we never transmitted" for "nothing
    acknowledged" — the scheduler latches its state on the latter and
    would otherwise skip the rest of the night.
    """
    live = CecAdapter.live()
    if not live:
        # Nothing to drive, so do not pay for the lock. Measured at
        # 0.3-1.4s of pure redis round-trip on the boards with no CEC
        # link, which is most of the fleet and every scheduled
        # transition on them.
        return 0, 0

    acknowledged = 0
    with _bus_lock() as locked:
        if not locked:
            raise CecBusyError('CEC bus busy; command not sent')
        for adapter in live:
            try:
                if adapter.set_power(on):
                    acknowledged += 1
            except CEC_ERRORS as exc:
                logger.warning(
                    'CEC power command failed on %s: %s', adapter.node, exc
                )
                continue
    return acknowledged, len(live)


# ---------------------------------------------------------------------
# Low-level plumbing
# ---------------------------------------------------------------------


def _cstr(raw: bytes) -> str:
    return raw.split(b'\0')[0].decode('utf-8', errors='replace')


@contextmanager
def _open_node(node: str) -> Iterator[int]:
    """Open a CEC node, always closing it.

    Opened blocking on purpose: ``CEC_ADAP_S_LOG_ADDRS`` and
    ``CEC_TRANSMIT`` are both bounded by the driver (the latter by the
    ``timeout`` field in ``struct cec_msg``), and the non-blocking
    variants would require draining results through ``CEC_DQEVENT`` for
    no benefit. ``_run_bounded`` is the backstop for a driver that
    never returns.
    """
    try:
        fd = os.open(node, os.O_RDWR)
    except OSError as exc:
        raise CecError(f'cannot open {node}: {exc}') from exc
    try:
        yield fd
    finally:
        os.close(fd)


def _claim_logical_address(fd: int) -> int:
    """Claim a playback-device logical address, returning the initiator.

    Raises ``CecError`` if the address cannot be claimed, rather than
    quietly falling back to the unregistered address (15). An
    unregistered initiator can still put bytes on the bus but cannot
    receive a directed reply, so a power *query* issued that way always
    looks like "nothing answered" — indistinguishable from a monitor
    with no CEC support, while actually meaning "we never got on the
    bus". Measured on the Pi 5: a real claim settles in ~145 ms and the
    subsequent query takes ~428 ms, whereas the unregistered path
    returns in ~0 ms, which is how this was caught.
    """
    payload = struct.pack(
        _FMT_LOG_ADDRS,
        b'\xff\xff\xff\xff',  # log_addr[4] — the kernel fills these in
        0,  # log_addr_mask
        CEC_OP_CEC_VERSION_1_4,
        1,  # num_log_addrs
        CEC_VENDOR_ID_NONE,
        0,  # flags
        _OSD_NAME,
        bytes([CEC_OP_PRIM_DEVTYPE_PLAYBACK, 0, 0, 0]),
        bytes([CEC_LOG_ADDR_TYPE_PLAYBACK, 0, 0, 0]),
        bytes([CEC_OP_ALL_DEVTYPE_PLAYBACK, 0, 0, 0]),
        b'\0' * 48,  # features
    )
    # Clear first, unconditionally. ``CEC_ADAP_S_LOG_ADDRS`` returns
    # EBUSY if the adapter is *already* configured — the kernel requires
    # a num_log_addrs=0 clear before accepting a new configuration.
    #
    # This is not hypothetical tidiness. libcec leaves the adapter
    # configured after its process exits (observed on the Pi 5 with the
    # shipped build: logical address mask 0x0002, LA 1 'Recording
    # Device 1', vendor 0x001582 'Pulse-Eight' — libcec's signature,
    # left behind by the 5-minutely display-power probe). Any device
    # upgrading from the libcec implementation therefore meets a
    # pre-configured adapter on first run, and without this clear every
    # query fails with EBUSY. A process of ours that died mid-operation
    # leaves the same residue.
    _clear_logical_addresses(fd)

    try:
        fcntl.ioctl(fd, CEC_ADAP_S_LOG_ADDRS, bytearray(payload))
    except OSError as exc:
        # EBUSY here now means another filehandle holds the adapter in
        # exclusive-initiator mode; anything else is the driver refusing
        # the configuration outright.
        raise CecError(
            f'could not configure a logical address: {exc}'
        ) from exc

    deadline = _CLAIM_TIMEOUT_S
    while deadline > 0:
        addr = _current_logical_address(fd)
        if addr is not None:
            return addr
        time.sleep(_CLAIM_POLL_S)
        deadline -= _CLAIM_POLL_S
    raise CecError(
        f'logical address not claimed within {_CLAIM_TIMEOUT_S:.0f}s'
    )


def _current_logical_address(fd: int) -> int | None:
    """The claimed logical address, or ``None`` while still unclaimed."""
    buf = bytearray(_SZ_LOG_ADDRS)
    try:
        fcntl.ioctl(fd, CEC_ADAP_G_LOG_ADDRS, buf)
    except OSError:
        return None
    log_addr, mask, _ver, num, _vendor, _flags = struct.unpack(
        _FMT_LOG_ADDRS, bytes(buf)
    )[:6]
    if not mask or not num:
        return None
    addr = log_addr[0]
    return None if addr == 0xFF else int(addr)


def _release_logical_address(fd: int) -> None:
    """Give the logical address back after an operation.

    Leaving it claimed would keep Anthias on the bus as a phantom
    playback device, which is exactly what the old libcec path did — it
    left every adapter configured as a Pulse-Eight 'Recording Device 1'
    between probes, which is what made the *next* configuration attempt
    fail with EBUSY.
    """
    _clear_logical_addresses(fd)


def _clear_logical_addresses(fd: int) -> None:
    """Return the adapter to the unconfigured state (num_log_addrs = 0).

    Best-effort: a failure here is not itself actionable, and the
    subsequent configure will report a real problem if one remains.
    """
    payload = struct.pack(
        _FMT_LOG_ADDRS,
        b'\xff\xff\xff\xff',
        0,
        0,
        0,  # num_log_addrs = 0 -> release
        CEC_VENDOR_ID_NONE,
        0,
        b'',
        b'\0' * 4,
        b'\0' * 4,
        b'\0' * 4,
        b'\0' * 48,
    )
    try:
        fcntl.ioctl(fd, CEC_ADAP_S_LOG_ADDRS, bytearray(payload))
    except OSError:
        pass


def _transmit(
    fd: int,
    initiator: int,
    destination: int,
    opcode: int,
    params: bytes = b'',
    reply: int = 0,
) -> tuple[int, int, bytes]:
    """Send one CEC message, optionally waiting for a specific reply.

    Returns ``(tx_status, rx_status, reply_bytes)``. The kernel bounds
    the wait using the ``timeout`` field, so this cannot hang on an
    unresponsive peer: a plain monitor NACKs and the ioctl returns with
    ``CEC_TX_STATUS_NACK | CEC_TX_STATUS_MAX_RETRIES``.
    """
    body = bytes([(initiator << 4) | destination, opcode]) + params
    buf = bytearray(_SZ_MSG)
    struct.pack_into(
        _FMT_MSG,
        buf,
        0,
        0,  # tx_ts
        0,  # rx_ts
        len(body),
        _REPLY_TIMEOUT_MS if reply else 0,
        0,  # sequence
        0,  # flags
        body.ljust(16, b'\0'),
        reply,
        0,  # rx_status
        0,  # tx_status
        0,  # tx_arb_lost_cnt
        0,  # tx_nack_cnt
        0,  # tx_low_drive_cnt
        0,  # tx_error_cnt
    )
    fcntl.ioctl(fd, CEC_TRANSMIT, buf)
    (
        _tx_ts,
        _rx_ts,
        length,
        _timeout,
        _sequence,
        _flags,
        msg,
        _reply,
        rx_status,
        tx_status,
        *_counts,
    ) = struct.unpack(_FMT_MSG, bytes(buf))
    return tx_status, rx_status, msg[:length]
