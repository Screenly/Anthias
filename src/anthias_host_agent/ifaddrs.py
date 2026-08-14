"""Enumerate the host's interface addresses using only the stdlib.

This replaces the ``netifaces`` / ``netifaces2`` dependency. Both were
compiled extensions: ``netifaces`` is an unmaintained C extension with
no wheels (it fails to build on any platform its 2021 sources don't
know about), and ``netifaces2`` is a Rust rewrite that has to ship a
wheel per architecture. The host agent used exactly one thing from
them — "list every interface and its IPv4/IPv6 addresses" — which the
kernel already answers over rtnetlink, so we ask it directly.

``RTM_GETADDR`` with ``NLM_F_DUMP`` returns one ``ifaddrmsg`` per
address (both families in a single dump when the request family is
``AF_UNSPEC``). The constants below are Linux uapi
(``linux/netlink.h``, ``linux/rtnetlink.h``) — a stable kernel ABI, not
something that drifts between releases.
"""

from __future__ import annotations

import errno
import os
import socket
import struct

# linux/netlink.h
_NLM_F_REQUEST = 0x001
_NLM_F_DUMP = 0x300
_NLMSG_ERROR = 0x2
_NLMSG_DONE = 0x3

# linux/rtnetlink.h
_RTM_GETADDR = 22
_IFA_ADDRESS = 1
_IFA_LOCAL = 2

# struct nlmsghdr {u32 len; u16 type; u16 flags; u32 seq; u32 pid;}
_NLMSG_HDR = struct.Struct('=IHHII')
# struct ifaddrmsg {u8 family; u8 prefixlen; u8 flags; u8 scope;
#                   u32 index;}
_IFADDRMSG = struct.Struct('=BBBBI')
# struct rtattr {u16 len; u16 type;}
_RTATTR = struct.Struct('=HH')
# struct nlmsgerr leads with `int error` (a negative errno).
_NLMSGERR_CODE = struct.Struct('=i')

# Netlink messages never exceed 64 KiB and the kernel never splits one
# across datagrams, so a single recv() always yields whole messages.
_RECV_SIZE = 65536

# A local dump is answered in microseconds; the timeout only exists so
# a wedged socket can't hang the host agent's subscriber loop forever.
_TIMEOUT_SECONDS = 5.0

_ADDRESS_SIZES = {socket.AF_INET: 4, socket.AF_INET6: 16}


def _align(length: int) -> int:
    """Round up to the 4-byte alignment netlink pads every TLV to."""
    return (length + 3) & ~3


def _iter_attributes(payload: bytes) -> list[tuple[int, bytes]]:
    """Split an ``rtattr`` run into ``(type, value)`` pairs.

    A truncated or zero-length attribute ends the run rather than
    raising: a malformed tail costs us the remaining attributes of one
    address, not the whole dump.
    """
    attributes = []
    offset = 0
    while offset + _RTATTR.size <= len(payload):
        length, attr_type = _RTATTR.unpack_from(payload, offset)
        if length < _RTATTR.size or offset + length > len(payload):
            break
        value = payload[offset + _RTATTR.size : offset + length]
        attributes.append((attr_type, value))
        offset += _align(length)
    return attributes


def _parse_address(payload: bytes) -> tuple[str, str] | None:
    """Turn one ``ifaddrmsg`` body into ``(interface, address)``."""
    if len(payload) < _IFADDRMSG.size:
        return None

    family, _prefixlen, _flags, _scope, index = _IFADDRMSG.unpack_from(payload)
    size = _ADDRESS_SIZES.get(family)
    if size is None:
        return None

    try:
        interface = socket.if_indextoname(index)
    except OSError:
        # The interface went away between the dump and this lookup.
        return None

    raw_local = None
    raw_address = None
    for attr_type, value in _iter_attributes(payload[_IFADDRMSG.size :]):
        if attr_type == _IFA_LOCAL:
            raw_local = value
        elif attr_type == _IFA_ADDRESS:
            raw_address = value

    # On a point-to-point link IFA_ADDRESS holds the *peer* address and
    # IFA_LOCAL holds ours, so IFA_LOCAL wins where both are present.
    # IPv6 only ever sends IFA_ADDRESS.
    raw = raw_local or raw_address
    if raw is None or len(raw) != size:
        return None

    return interface, socket.inet_ntop(family, raw)


def _build_dump_request() -> bytes:
    """A ``RTM_GETADDR`` dump for every address family."""
    body = _IFADDRMSG.pack(socket.AF_UNSPEC, 0, 0, 0, 0)
    header = _NLMSG_HDR.pack(
        _NLMSG_HDR.size + len(body),
        _RTM_GETADDR,
        _NLM_F_REQUEST | _NLM_F_DUMP,
        1,
        0,
    )
    return header + body


def parse_dump(data: bytes) -> tuple[list[tuple[str, str]], bool]:
    """Parse one netlink datagram into addresses plus a done flag.

    Split out from :func:`interface_addresses` so the wire format can
    be tested without a live netlink socket.

    Raises ``OSError`` when the kernel answers ``NLMSG_ERROR``.
    """
    addresses: list[tuple[str, str]] = []
    offset = 0

    while offset + _NLMSG_HDR.size <= len(data):
        msg_len, msg_type, _flags, _seq, _pid = _NLMSG_HDR.unpack_from(
            data, offset
        )
        if msg_len < _NLMSG_HDR.size or offset + msg_len > len(data):
            break
        if msg_type == _NLMSG_DONE:
            return addresses, True
        if msg_type == _NLMSG_ERROR:
            # struct nlmsgerr leads with a negative errno. Read it out
            # of this message's own body so a truncated tail can't be
            # mistaken for an error code borrowed from the next message,
            # and still fail as an OSError when there is no code at all.
            body = data[offset + _NLMSG_HDR.size : offset + msg_len]
            if len(body) < _NLMSGERR_CODE.size:
                raise OSError(errno.EPROTO, 'truncated netlink error message')
            (error,) = _NLMSGERR_CODE.unpack_from(body)
            code = -error
            raise OSError(code, os.strerror(code))

        parsed = _parse_address(
            data[offset + _NLMSG_HDR.size : offset + msg_len]
        )
        if parsed is not None:
            addresses.append(parsed)
        offset += _align(msg_len)

    return addresses, False


def interface_addresses() -> dict[str, list[str]]:
    """Map every interface name to its IPv4 + IPv6 addresses.

    Addresses come back in kernel dump order, as plain strings with no
    scope suffix (``fe80::1``, not ``fe80::1%eth0``).
    """
    if not hasattr(socket, 'AF_NETLINK'):
        raise OSError('rtnetlink address enumeration requires Linux')

    result: dict[str, list[str]] = {}
    with socket.socket(
        socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE
    ) as sock:
        sock.settimeout(_TIMEOUT_SECONDS)
        sock.bind((0, 0))
        sock.send(_build_dump_request())

        while True:
            data = sock.recv(_RECV_SIZE)
            if not data:
                break
            addresses, done = parse_dump(data)
            for interface, address in addresses:
                result.setdefault(interface, []).append(address)
            if done:
                break

    return result
