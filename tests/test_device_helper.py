from unittest import mock

import pytest

from lib import device_helper


PI4_CPUINFO = """\
processor	: 0
BogoMIPS	: 108.00
Features	: fp asimd evtstrm crc32 cpuid
CPU implementer	: 0x41

processor	: 1
BogoMIPS	: 108.00

processor	: 2

processor	: 3

Hardware	: BCM2711
Revision	: c03114
Serial		: 100000004a4f5b8c
Model		: Raspberry Pi 4 Model B Rev 1.4
"""


def test_parse_cpu_info_extracts_fields() -> None:
    m = mock.mock_open(read_data=PI4_CPUINFO)
    with mock.patch('builtins.open', m):
        info = device_helper.parse_cpu_info()
    assert info['cpu_count'] == 4
    assert info['hardware'] == 'BCM2711'
    assert info['revision'] == 'c03114'
    assert info['serial'] == '100000004a4f5b8c'
    assert info['model'] == 'Raspberry Pi 4 Model B Rev 1.4'


def test_parse_cpu_info_minimal() -> None:
    m = mock.mock_open(read_data='processor : 0\n')
    with mock.patch('builtins.open', m):
        info = device_helper.parse_cpu_info()
    assert info['cpu_count'] == 1
    # No Hardware/Model/etc. → only cpu_count populated.
    assert 'hardware' not in info


@pytest.mark.parametrize(
    'content,expected',
    [
        ('Raspberry Pi 5 Model B Rev 1.0', 'pi5'),
        ('Compute Module 5', 'pi5'),
        ('Raspberry Pi 4 Model B Rev 1.4', 'pi4'),
        ('Compute Module 4', 'pi4'),
        ('Raspberry Pi 3 Model B Plus', 'pi3'),
        ('Compute Module 3+', 'pi3'),
        ('Raspberry Pi 2 Model B', 'pi2'),
        ('Raspberry Pi Model B Plus Rev 1.2', 'pi1'),
    ],
)
def test_get_device_type_from_dt_model(content: str, expected: str) -> None:
    m = mock.mock_open(read_data=content)
    with mock.patch('builtins.open', m):
        assert device_helper.get_device_type() == expected


def test_get_device_type_falls_back_to_x86() -> None:
    with mock.patch(
        'builtins.open', side_effect=FileNotFoundError('no such file')
    ):
        assert device_helper.get_device_type() == 'x86'
