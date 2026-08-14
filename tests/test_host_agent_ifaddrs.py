"""Tests for the stdlib rtnetlink address enumeration.

``anthias_host_agent.ifaddrs`` replaces the ``netifaces`` /
``netifaces2`` compiled extensions with a ``RTM_GETADDR`` dump parsed
in pure Python. The splash page's "Detecting network…" state hangs
forever if this returns nothing, so we pin both halves: the wire-format
parser (against synthetic datagrams, no socket needed) and
``get_ip_addresses``'s interface-prefix + link-local filtering.
"""

from __future__ import annotations

import errno
import socket
import struct
from unittest import mock

import pytest

from anthias_host_agent.ifaddrs import (
    _IFADDRMSG,
    _NLMSG_HDR,
    _RTATTR,
    interface_addresses,
    parse_dump,
)

# The parser resolves the interface index through if_indextoname, so the
# synthetic datagrams have to name an interface that really exists.
# Index 1 is the loopback everywhere; only its name is platform-specific
# (`lo` on Linux, `lo0` on the BSDs), so derive the name from the index
# rather than hardcoding it — that keeps the pure-parser tests running on
# a non-Linux dev host, where rtnetlink itself is unavailable.
_LO_INDEX = 1
_LO = socket.if_indextoname(_LO_INDEX)

# rtnetlink is Linux-only: socket.AF_NETLINK doesn't exist elsewhere and
# interface_addresses() refuses to run without it.
requires_rtnetlink = pytest.mark.skipif(
    not hasattr(socket, 'AF_NETLINK'),
    reason='rtnetlink address enumeration requires Linux',
)

_RTM_NEWADDR = 20
_IFA_ADDRESS = 1
_IFA_LOCAL = 2
_NLMSG_ERROR = 2
_NLMSG_DONE = 3

# AF_PACKET carries the interface's MAC. netifaces reported it under
# its own key and the host agent never asked for it, so the parser has
# to drop it rather than hand a 6-byte payload to inet_ntop.
_AF_PACKET = 17


def _attribute(attr_type: int, value: bytes) -> bytes:
    """Pack one rtattr, padded to netlink's 4-byte alignment."""
    length = _RTATTR.size + len(value)
    padding = b'\x00' * (-length % 4)
    return _RTATTR.pack(length, attr_type) + value + padding


def _message(msg_type: int, body: bytes) -> bytes:
    """Wrap a body in an nlmsghdr, padded to 4-byte alignment."""
    length = _NLMSG_HDR.size + len(body)
    padding = b'\x00' * (-length % 4)
    return _NLMSG_HDR.pack(length, msg_type, 0, 1, 0) + body + padding


def _address_message(
    family: int,
    index: int,
    attributes: bytes,
    prefixlen: int = 24,
) -> bytes:
    body = _IFADDRMSG.pack(family, prefixlen, 0, 0, index) + attributes
    return _message(_RTM_NEWADDR, body)


class TestParseDump:
    def test_parses_ipv4_and_ipv6_from_one_datagram(self) -> None:
        data = _address_message(
            socket.AF_INET,
            _LO_INDEX,
            _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
            ),
        ) + _address_message(
            socket.AF_INET6,
            _LO_INDEX,
            _attribute(
                _IFA_ADDRESS, socket.inet_pton(socket.AF_INET6, 'fd00::5')
            ),
            prefixlen=64,
        )

        addresses, done = parse_dump(data)

        assert addresses == [(_LO, '10.0.0.5'), (_LO, 'fd00::5')]
        assert done is False

    def test_ifa_local_wins_over_the_peer_address(self) -> None:
        """On a point-to-point link IFA_ADDRESS is the *peer*.

        Reporting the peer would publish the far end of a PPP/WireGuard
        tunnel as the device's own IP on the splash screen.
        """
        data = _address_message(
            socket.AF_INET,
            _LO_INDEX,
            _attribute(
                _IFA_ADDRESS, socket.inet_pton(socket.AF_INET, '192.0.2.99')
            )
            + _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '192.0.2.1')
            ),
        )

        addresses, _done = parse_dump(data)

        assert addresses == [(_LO, '192.0.2.1')]

    def test_ipv6_falls_back_to_ifa_address(self) -> None:
        """IPv6 only ever sends IFA_ADDRESS, never IFA_LOCAL."""
        data = _address_message(
            socket.AF_INET6,
            _LO_INDEX,
            _attribute(
                _IFA_ADDRESS, socket.inet_pton(socket.AF_INET6, '2001:db8::1')
            ),
            prefixlen=64,
        )

        addresses, _done = parse_dump(data)

        assert addresses == [(_LO, '2001:db8::1')]

    def test_skips_link_layer_addresses(self) -> None:
        data = _address_message(
            _AF_PACKET,
            _LO_INDEX,
            _attribute(_IFA_ADDRESS, b'\x00\x11\x22\x33\x44\x55'),
        ) + _address_message(
            socket.AF_INET,
            _LO_INDEX,
            _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
            ),
        )

        addresses, _done = parse_dump(data)

        assert addresses == [(_LO, '10.0.0.5')]

    def test_skips_addresses_with_no_usable_attribute(self) -> None:
        """An ifaddrmsg carrying neither IFA_LOCAL nor IFA_ADDRESS."""
        data = _address_message(socket.AF_INET, _LO_INDEX, b'')

        addresses, _done = parse_dump(data)

        assert addresses == []

    def test_skips_wrong_length_payload(self) -> None:
        """A 16-byte value on an AF_INET record must not reach inet_ntop."""
        data = _address_message(
            socket.AF_INET, _LO_INDEX, _attribute(_IFA_LOCAL, b'\x00' * 16)
        )

        addresses, _done = parse_dump(data)

        assert addresses == []

    def test_unknown_interface_index_is_dropped(self) -> None:
        """The interface can disappear between the dump and the lookup."""
        data = _address_message(
            socket.AF_INET,
            9999,
            _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
            ),
        )

        addresses, _done = parse_dump(data)

        assert addresses == []

    def test_nlmsg_done_ends_the_dump(self) -> None:
        data = (
            _address_message(
                socket.AF_INET,
                _LO_INDEX,
                _attribute(
                    _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
                ),
            )
            + _message(_NLMSG_DONE, b'')
            # Anything after NLMSG_DONE must be ignored.
            + _address_message(
                socket.AF_INET,
                _LO_INDEX,
                _attribute(
                    _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.6')
                ),
            )
        )

        addresses, done = parse_dump(data)

        assert addresses == [(_LO, '10.0.0.5')]
        assert done is True

    def test_nlmsg_error_raises_oserror(self) -> None:
        # struct nlmsgerr leads with a negative errno.
        data = _message(_NLMSG_ERROR, struct.pack('=i', -13))

        with pytest.raises(OSError) as excinfo:
            parse_dump(data)

        assert excinfo.value.errno == 13

    def test_nlmsg_error_without_a_code_still_raises_oserror(self) -> None:
        """An NLMSG_ERROR whose body is missing or truncated.

        The contract is OSError, so a short nlmsgerr must not surface as
        a struct.error out of unpack_from.
        """
        for body in (b'', struct.pack('=h', -13)):
            data = _message(_NLMSG_ERROR, body)

            with pytest.raises(OSError) as excinfo:
                parse_dump(data)

            assert excinfo.value.errno == errno.EPROTO

    def test_nlmsg_error_code_is_not_read_from_the_next_message(self) -> None:
        """A truncated nlmsgerr must not borrow the following bytes."""
        data = _message(_NLMSG_ERROR, b'') + _address_message(
            socket.AF_INET,
            _LO_INDEX,
            _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
            ),
        )

        with pytest.raises(OSError) as excinfo:
            parse_dump(data)

        assert excinfo.value.errno == errno.EPROTO

    def test_truncated_message_stops_parsing(self) -> None:
        """A short final message ends the run instead of raising."""
        data = _address_message(
            socket.AF_INET,
            _LO_INDEX,
            _attribute(
                _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
            ),
        )

        addresses, done = parse_dump(data[:-4])

        assert addresses == []
        assert done is False

    def test_truncated_attribute_keeps_the_earlier_ones(self) -> None:
        good = _attribute(
            _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
        )
        data = _address_message(
            socket.AF_INET, _LO_INDEX, good + _RTATTR.pack(64, _IFA_ADDRESS)
        )

        addresses, _done = parse_dump(data)

        assert addresses == [(_LO, '10.0.0.5')]


@requires_rtnetlink
class TestInterfaceAddresses:
    def test_live_dump_reports_loopback(self) -> None:
        """Smoke test against the real kernel.

        Every Linux netns has ``lo`` with 127.0.0.1, container or not,
        so this is safe in CI and is the check that would actually
        catch a broken request/socket setup.
        """
        result = interface_addresses()

        assert '127.0.0.1' in result[_LO]

    def test_groups_multiple_addresses_under_one_interface(self) -> None:
        """Secondary addresses stack rather than overwrite."""
        datagram = (
            _address_message(
                socket.AF_INET,
                _LO_INDEX,
                _attribute(
                    _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.5')
                ),
            )
            + _address_message(
                socket.AF_INET,
                _LO_INDEX,
                _attribute(
                    _IFA_LOCAL, socket.inet_pton(socket.AF_INET, '10.0.0.6')
                ),
            )
            + _message(_NLMSG_DONE, b'')
        )

        with mock.patch(
            'anthias_host_agent.ifaddrs.socket.socket'
        ) as socket_factory:
            sock = socket_factory.return_value.__enter__.return_value
            sock.recv.return_value = datagram
            result = interface_addresses()

        assert result == {_LO: ['10.0.0.5', '10.0.0.6']}


class TestGetIpAddresses:
    @pytest.mark.parametrize(
        ('addresses', 'expected'),
        [
            # Loopback and the docker bridge are not "the device's IP".
            (
                {
                    'lo': ['127.0.0.1', '::1'],
                    'docker0': ['172.17.0.1'],
                    'eth0': ['192.168.1.50'],
                },
                ['192.168.1.50'],
            ),
            # Link-local v4 (169.254/16) and v6 (fe80::/10) are dropped
            # even on a supported interface — they're what a NIC self-
            # assigns when DHCP never answered.
            (
                {
                    'eth0': [
                        '192.168.1.50',
                        '169.254.3.4',
                        'fe80::1',
                        '2001:db8::5',
                    ]
                },
                ['192.168.1.50', '2001:db8::5'],
            ),
            # Every prefix in SUPPORTED_INTERFACES, including `end0`
            # (the Rock Pi 4's GMAC) which the splash page needs.
            (
                {
                    'wlan0': ['192.168.1.10'],
                    'wlp2s0': ['192.168.1.11'],
                    'enp0s3': ['192.168.1.12'],
                    'eno1': ['192.168.1.13'],
                    'ens18': ['192.168.1.14'],
                    'end0': ['192.168.1.15'],
                },
                [
                    '192.168.1.10',
                    '192.168.1.11',
                    '192.168.1.12',
                    '192.168.1.13',
                    '192.168.1.14',
                    '192.168.1.15',
                ],
            ),
            ({}, []),
        ],
    )
    def test_filters_to_real_routable_interface_addresses(
        self, addresses: dict[str, list[str]], expected: list[str]
    ) -> None:
        from anthias_host_agent.__main__ import get_ip_addresses

        with mock.patch(
            'anthias_host_agent.__main__.interface_addresses',
            return_value=addresses,
        ):
            assert get_ip_addresses() == expected
