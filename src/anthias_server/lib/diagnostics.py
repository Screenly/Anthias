#!/usr/bin/env python

import os
import subprocess
import sys
from datetime import datetime

from anthias_common import device_helper, utils


_CEC_QUERY_SCRIPT = """
import sys
try:
    import cec
    cec.init()
    tv = cec.Device(cec.CECDEVICE_TV)
except Exception:
    sys.stdout.write('CEC error')
    sys.exit(0)
try:
    sys.stdout.write('True' if tv.is_on() else 'False')
except IOError:
    sys.stdout.write('Unknown')
"""


def get_display_power() -> str | bool:
    """
    Queries the TV using CEC.

    The CEC stack can block inside libcec (no HDMI link, TV asleep,
    adapter unresponsive) in a C call that ignores Python signals,
    which would tie up the celery worker until it hits its hard
    time_limit and gets SIGKILL'd. Run the query in a subprocess so
    we can enforce a timeout and recover cleanly.
    """
    try:
        result = subprocess.run(
            [sys.executable, '-c', _CEC_QUERY_SCRIPT],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return 'CEC error'

    output = result.stdout.decode('utf-8', errors='replace').strip()
    if output == 'True':
        return True
    if output == 'False':
        return False
    return output or 'CEC error'


def get_uptime() -> float:
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds


def get_load_avg() -> dict[str, float]:
    """
    Returns load average rounded to two digits.
    """

    load_avg: dict[str, float] = {}
    get_load_avg = os.getloadavg()

    load_avg['1 min'] = round(get_load_avg[0], 2)
    load_avg['5 min'] = round(get_load_avg[1], 2)
    load_avg['15 min'] = round(get_load_avg[2], 2)

    return load_avg


def get_git_branch() -> str | None:
    return os.getenv('GIT_BRANCH')


def get_git_short_hash() -> str | None:
    return os.getenv('GIT_SHORT_HASH')


def get_git_hash() -> str | None:
    return os.getenv('GIT_HASH')


def try_connectivity() -> list[str]:
    urls = [
        'http://www.google.com',
        'http://www.bbc.co.uk',
        'https://www.google.com',
        'https://www.bbc.co.uk',
    ]
    result = []
    for url in urls:
        if utils.url_fails(url):
            result.append('{}: Error'.format(url))
        else:
            result.append('{}: OK'.format(url))
    return result


def get_utc_isodate() -> str:
    return datetime.isoformat(datetime.utcnow())


def get_debian_version() -> str:
    debian_version = '/etc/debian_version'
    if os.path.isfile(debian_version):
        with open(debian_version, 'r') as f:
            for line in f:
                return str(line).strip()
        return 'Unable to get Debian version.'
    else:
        return 'Unable to get Debian version.'


def get_raspberry_code() -> int | str:
    return device_helper.parse_cpu_info().get('hardware', 'Unknown')


def get_raspberry_model() -> int | str:
    return device_helper.parse_cpu_info().get('model', 'Unknown')
