import os
from datetime import datetime

try:
    import cec
except ImportError:
    cec = None

from lib import device_helper

from . import utils


def get_display_power() -> str | bool:
    """
    Queries the TV using CEC
    """
    if cec is None:
        return 'CEC not available'

    tv_status = None

    try:
        cec.init()
        tv = cec.Device(cec.CECDEVICE_TV)
    except Exception:
        return 'CEC error'

    try:
        tv_status = tv.is_on()
    except IOError:
        return 'Unknown'

    return tv_status


def get_uptime() -> float:
    with open('/proc/uptime') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds


def get_load_avg() -> dict[str, float]:
    """
    Returns load average rounded to two digits.
    """

    load_avg = {}
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
            result.append(f'{url}: Error')
        else:
            result.append(f'{url}: OK')
    return result


def get_utc_isodate() -> str:
    return datetime.isoformat(datetime.utcnow())


def get_debian_version() -> str:
    debian_version = '/etc/debian_version'
    if os.path.isfile(debian_version):
        with open(debian_version) as f:
            for line in f:
                return str(line).strip()
    else:
        return 'Unable to get Debian version.'


def get_raspberry_code() -> str:
    return device_helper.parse_cpu_info().get('hardware', 'Unknown')


def get_raspberry_model() -> str:
    return device_helper.parse_cpu_info().get('model', 'Unknown')
