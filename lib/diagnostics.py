#!/usr/bin/env python

from __future__ import absolute_import, unicode_literals

import os
from builtins import str
from datetime import datetime

import cec

from lib import device_helper

from . import utils


def get_display_power():
    """
    Queries the TV using CEC
    """
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


def get_uptime():
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds


def get_load_avg():
    """
    Returns load average rounded to two digits.
    """

    load_avg = {}
    get_load_avg = os.getloadavg()

    load_avg['1 min'] = round(get_load_avg[0], 2)
    load_avg['5 min'] = round(get_load_avg[1], 2)
    load_avg['15 min'] = round(get_load_avg[2], 2)

    return load_avg


def get_git_branch():
    return os.getenv('GIT_BRANCH')


def get_git_short_hash():
    return os.getenv('GIT_SHORT_HASH')


def get_git_hash():
    return os.getenv('GIT_HASH')


def try_connectivity():
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


def get_utc_isodate():
    return datetime.isoformat(datetime.utcnow())


def get_debian_version():
    debian_version = '/etc/debian_version'
    if os.path.isfile(debian_version):
        with open(debian_version, 'r') as f:
            for line in f:
                return str(line).strip()
    else:
        return 'Unable to get Debian version.'


def get_raspberry_code():
    return device_helper.parse_cpu_info().get('hardware', 'Unknown')


def get_raspberry_model():
    return device_helper.parse_cpu_info().get('model', 'Unknown')
