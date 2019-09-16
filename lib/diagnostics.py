#!/usr/bin/env python

import netifaces
import os
import sh
import socket
import sqlite3
import re
import utils
from pprint import pprint
from uptime import uptime
from datetime import datetime


def parse_cpu_info():
    cpu_info = {
        'cpu_count': 0
    }

    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            try:
                key = line.split(':')[0].strip()
                value = line.split(':')[1].strip()
            except Exception:
                pass

            if key == 'processor':
                cpu_info['cpu_count'] += 1

            if key in ['Serial', 'Hardware', 'Revision', 'model name']:
                cpu_info[key.lower()] = value
    return cpu_info


def get_kernel_modules():
    modules = []
    try:
        for line in sh.lsmod():
            if 'Module' not in line:
                modules.append(line.split()[0])
        return modules
    except Exception:
        return 'Unable to run lsmod.'


def get_gpu_version():
    try:
        version = sh.vcgencmd('version')
        for line in version:
            if 'version' in line:
                return line.strip().replace('version ', '')
    except Exception:
        return 'Unable to run vcgencmd.'


def get_monitor_status():
    try:
        return sh.tvservice('-s').stdout.strip()
    except Exception:
        return 'Unable to run tvservice.'


def get_display_power():
    try:
        display_status = sh.vcgencmd('display_power').stdout.strip().split('=')
        if display_status[1] == '1':
            return 'On'
        elif display_status[1] == '0':
            return 'Off'
        else:
            return 'Unknown'
    except Exception:
        return 'Unable to determine display power.'


def get_network_interfaces():
    if_data = {}
    for interface in netifaces.interfaces():
        if_data[interface] = netifaces.ifaddresses(interface)
    return if_data


def get_uptime():
    return uptime()


def get_playlist():
    screenly_db = os.path.join(os.getenv('HOME'), '.screenly/screenly.db')
    playlist = []
    if os.path.isfile(screenly_db):
        conn = sqlite3.connect(screenly_db)
        c = conn.cursor()
        for row in c.execute('SELECT * FROM assets;'):
            playlist.append(row)
        c.close
    return playlist


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
    screenly_path = os.path.join(os.getenv('HOME'), 'screenly', '.git')
    try:
        get_hash = sh.git(
            '--git-dir={}'.format(screenly_path),
            'rev-parse',
            '--abbrev-ref',
            'HEAD'
        )
        return get_hash.stdout.strip()
    except Exception:
        return 'Unable to get git branch.'


def get_git_short_hash():
    screenly_path = os.path.join(os.getenv('HOME'), 'screenly', '.git')
    try:
        get_hash = sh.git(
            '--git-dir={}'.format(screenly_path),
            'rev-parse',
            '--short',
            'HEAD'
        )
        return get_hash.stdout.strip()
    except Exception:
        return 'Unable to get git hash.'


def get_git_hash():
    screenly_path = os.path.join(os.getenv('HOME'), 'screenly', '.git')
    try:
        get_hash = sh.git(
            '--git-dir={}'.format(screenly_path),
            'rev-parse',
            'HEAD'
        )
        return get_hash.stdout.strip()
    except Exception:
        return 'Unable to get git hash.'


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


def ntp_status():
    query_ntp = sh.ntpq('-p')
    return query_ntp.stdout


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
    matches = re.findall(r'\:(.*)', sh.grep('Revision', '/proc/cpuinfo').stdout)
    if matches:
        return matches[0].strip()


def get_raspberry_model(raspberry_code):
    """
    Data source
    https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    """
    models = {
        '900021': 'Model A+',
        '900032': 'Model B+',
        '900092': 'Model Zero',
        '920092': 'Model Zero',
        '900093': 'Model Zero',
        '9000c1': 'Model Zero W',
        '920093': 'Model Zero',
        'a01040': 'Model 2B',
        'a01041': 'Model 2B',
        'a02082': 'Model 3B',
        'a020a0': 'Model CM3',
        'a21041': 'Model 2B',
        'a22042': 'Model 2B (with BCM2837)',
        '9020e0': 'Model 3A+',
        'a22082': 'Model 3B',
        'a32082': 'Model 3B',
        'a52082': 'Model 3B',
        'a22083': 'Model 3B',
        'a020d3': 'Model 3B+',
        'a03111': 'Model 4B',
        'b03111': 'Model 4B',
        'c03111': 'Model 4B',
        '900061': 'Model CM',
        'a220a0': 'Model CM3',
        'a02100': 'Model CM3+'
    }

    return models.get(raspberry_code, 'Unable to determine raspberry model.')


def get_raspberry_revision(raspberry_code):
    """
    Data source
    https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    """
    revisions = {
        '900021': '1.1',
        '900032': '1.2',
        '900092': '1.2',
        '920092': '1.2',
        '900093': '1.3',
        '9000c1': '1.1',
        '920093': '1.3',
        'a01040': '1.0',
        'a01041': '1.1',
        'a02082': '1.2',
        'a020a0': '1.0',
        'a21041': '1.1',
        'a22042': '1.2',
        '9020e0': '1.0',
        'a22082': '1.2',
        'a32082': '1.2',
        'a52082': '1.2',
        'a22083': '1.3',
        'a020d3': '1.3',
        'a03111': '1.1',
        'b03111': '1.1',
        'c03111': '1.1',
        '900061': '1.1',
        'a220a0': '1.0',
        'a02100': '1.0'
    }

    return revisions.get(raspberry_code, 'Unable to determine raspberry revision.')


def get_raspberry_ram(raspberry_code):
    """
    Data source
    https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    """
    rams = {
        '900021': '512 MB',
        '900032': '512 MB',
        '900092': '512 MB',
        '920092': '512 MB',
        '900093': '512 MB',
        '9000c1': '512 MB',
        '920093': '512 MB',
        'a01040': '1 GB',
        'a01041': '1 GB',
        'a02082': '1 GB',
        'a020a0': '1 GB',
        'a21041': '1 GB',
        'a22042': '1 GB',
        '9020e0': '512MB',
        'a22082': '1 GB',
        'a32082': '1 GB',
        'a52082': '1 GB',
        'a22083': '1GB',
        'a020d3': '1 GB',
        'a03111': '1GB',
        'b03111': '2GB',
        'c03111': '4GB',
        '900061': '512MB',
        'a220a0': '1GB',
        'a02100': '1GB'
    }

    return rams.get(raspberry_code, 'Unable to determine raspberry RAM.')


def get_raspberry_manufacturer(raspberry_code):
    """
    Data source
    https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    """
    manufacturers = {
        '900021': 'Sony UK',
        '900032': 'Sony UK',
        '900092': 'Sony UK',
        '920092': 'Embest',
        '900093': 'Sony UK',
        '9000c1': 'Sony UK',
        '920093': 'Embest',
        'a01040': 'Sony UK',
        'a01041': 'Sony UK',
        'a02082': 'Sony UK',
        'a020a0': 'Sony UK',
        'a21041': 'Embest',
        'a22042': 'Embest',
        '9020e0': 'Sony UK',
        'a22082': 'Embest',
        'a32082': 'Sony Japan',
        'a52082': 'Stadium',
        'a22083': 'Embest',
        'a020d3': 'Sony UK',
        'a03111': 'Sony UK',
        'b03111': 'Sony UK',
        'c03111': 'Sony UK',
        '900061': 'Sony UK',
        'a220a0': 'Embest',
        'a02100': 'Sony UK'
    }

    return manufacturers.get(raspberry_code, 'Unable to determine raspberry manufacturer.')


def compile_report():
    report = {}
    report['cpu_info'] = parse_cpu_info()
    report['uptime'] = get_uptime()
    report['kernel_modules'] = get_kernel_modules()
    report['monitor'] = get_monitor_status()
    report['display_power'] = get_display_power()
    report['ifconfig'] = get_network_interfaces()
    report['hostname'] = socket.gethostname()
    report['playlist'] = get_playlist()
    report['git_hash'] = get_git_hash()
    report['connectivity'] = try_connectivity()
    report['loadavg'] = get_load_avg()
    report['ntp_status'] = ntp_status()
    report['utc_isodate'] = get_utc_isodate()
    report['debian_version'] = get_debian_version()
    report['gpu_version'] = get_gpu_version()

    return report


def main():
    pprint(compile_report())


if __name__ == "__main__":
    main()
