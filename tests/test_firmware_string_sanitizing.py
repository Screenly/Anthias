"""Sanitising of firmware-supplied strings.

Device-tree properties, DMI/SMBIOS fields and ``/proc/cpuinfo`` lines
are read, not authored, by us. A board vendor's DTB, an OEM's SMBIOS
tables, or a hypervisor's synthetic DMI decide the contents, and the
result reaches the System Info card, ``/api/v2/info``, a Redis value,
log lines and the outbound telemetry payload.

Escaping at those sinks is unchanged and still does the injection
work (Django autoescapes; DRF JSON-encodes). These tests pin what no
sink covers: bounded length, no control characters, no bidi /
zero-width trickery — and, just as important, that every string a
real board reports passes through untouched.
"""

from __future__ import annotations

from unittest import mock

import pytest

from anthias_common.device_helper import (
    _MAX_FIRMWARE_STRING_LEN,
    parse_cpu_info,
    read_device_tree_model,
    sanitize_firmware_string,
)


@pytest.mark.parametrize(
    'value',
    [
        'Raspberry Pi 5 Model B Rev 1.0',
        'FriendlyElec NanoPi R3S LTS',
        'Radxa ROCK Pi 4B',
        'Intel Core i7-9700K @ 3.60GHz',
        'AMD Ryzen 7 5700G',
        'ASUSTeK Computer INC.',
        # Non-ASCII vendor names are legitimate and must survive.
        'Möbelfabrik Signage GmbH',
    ],
)
def test_real_board_strings_pass_through_unchanged(value: str) -> None:
    assert sanitize_firmware_string(value) == value


def test_stops_at_the_nul_terminator() -> None:
    """Device-tree properties are NUL-terminated, and a list property
    packs several strings into one buffer. Concatenating across the
    terminator would invent a board name that doesn't exist."""
    assert (
        sanitize_firmware_string('NanoPi R3S\x00rockchip,rk3566\x00')
        == 'NanoPi R3S'
    )


def test_drops_terminal_control_sequences() -> None:
    """An ANSI escape in a model string would repaint an operator's
    terminal when they read the host_agent's journal."""
    assert sanitize_firmware_string('Evil\x1b[2JBoard\x07') == 'Evil[2JBoard'


def test_drops_bidi_and_zero_width_characters() -> None:
    """The class of character that makes a label render as something
    other than what it says.

    Built with chr() rather than literals on purpose: ruff's own
    PLE2502/PLE2515 rules reject unescaped bidi and zero-width
    characters in source, and the formatter turns \\u escapes back
    into literals — which is the same obfuscation risk this function
    exists to strip, so the test shouldn't smuggle one into the repo.
    """
    rtl_override = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
    pop_directional = chr(0x202C)  # POP DIRECTIONAL FORMATTING
    zero_width_space = chr(0x200B)

    spoofed = f'Pi{rtl_override}5 ledoM{pop_directional}'
    assert sanitize_firmware_string(spoofed) == 'Pi5 ledoM'
    assert sanitize_firmware_string(f'Nano{zero_width_space}Pi') == 'NanoPi'


def test_folds_whitespace_without_welding_words() -> None:
    assert (
        sanitize_firmware_string('  NanoPi \n\t R3S \r\n LTS  ')
        == 'NanoPi R3S LTS'
    )


def test_caps_the_length() -> None:
    """Nothing else bounds these strings, and they are re-rendered on
    every System Info page load and every /api/v2/info response."""
    out = sanitize_firmware_string('A' * 10_000)
    assert len(out) == _MAX_FIRMWARE_STRING_LEN


def test_html_is_left_to_the_template_layer() -> None:
    """The sanitiser deliberately does not strip or escape markup —
    Django autoescapes the card and DRF JSON-encodes the API. Pinning
    this stops a future change from mistaking it for the XSS defence
    and 'helpfully' double-escaping every board name."""
    assert (
        sanitize_firmware_string('<script>alert(1)</script>')
        == '<script>alert(1)</script>'
    )


def test_device_tree_read_is_bounded() -> None:
    """The cap is applied at the read, so an oversized property never
    lands in memory whole."""
    handle = mock.MagicMock()
    handle.read.return_value = b'NanoPi R3S LTS\x00'
    opener = mock.MagicMock()
    opener.return_value.__enter__.return_value = handle
    with mock.patch('anthias_common.device_helper.open', opener, create=True):
        assert read_device_tree_model() == 'NanoPi R3S LTS'
    # Called with an explicit byte budget rather than read().
    (size,), _ = handle.read.call_args
    assert size > 0


def test_cpuinfo_model_is_sanitized() -> None:
    """The Pi path: firmware sources this line from the device tree,
    and it reaches the card, the API and the telemetry payload.

    Same chr() construction as the bidi test above, for the same
    reason — no raw control character goes into the source file.
    """
    esc = chr(0x1B)  # ESC, the lead byte of an ANSI sequence
    rtl_override = chr(0x202E)
    sample = (
        'processor\t: 0\n'
        f'Model\t\t: Raspberry Pi 5{esc}[31m Model B{rtl_override} Rev 1.0\n'
    )
    with mock.patch(
        'anthias_common.device_helper.open',
        mock.mock_open(read_data=sample),
        create=True,
    ):
        info = parse_cpu_info()
    assert info['model'] == 'Raspberry Pi 5[31m Model B Rev 1.0'


def test_sentry_board_model_tag_is_sanitized() -> None:
    """The Sentry ``board_model`` tag is another sink for a string we
    don't author, and settings.py used to decode the device tree
    itself with only a NUL/whitespace strip. It now shares the same
    bounded, sanitised read as everything else."""
    from anthias_server.django_project.settings import get_board_model

    esc = chr(0x1B)
    payload = f'NanoPi{esc}[2J R3S{"!" * 400}'.encode()
    with mock.patch(
        'anthias_common.device_helper.open',
        mock.mock_open(read_data=payload),
        create=True,
    ):
        tag = get_board_model('/proc/device-tree/model')
    assert esc not in tag
    assert len(tag) <= _MAX_FIRMWARE_STRING_LEN


def test_read_firmware_file_missing_path_is_empty() -> None:
    from anthias_common.device_helper import read_firmware_file

    with mock.patch(
        'anthias_common.device_helper.open',
        side_effect=FileNotFoundError(),
        create=True,
    ):
        assert read_firmware_file('/nope') == ''
