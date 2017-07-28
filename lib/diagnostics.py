#!/usr/bin/env python

import netifaces
import os
import sh
import socket
import sqlite3
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
            except:
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
    except:
        return 'Unable to run lsmod.'


def get_gpu_version():
    try:
        version = sh.vcgencmd('version')
        for line in version:
            if 'version' in line:
                return line.strip().replace('version ', '')
    except:
        return 'Unable to run vcgencmd.'


def get_monitor_status():
    try:
        return sh.tvservice('-s').stdout.strip()
    except:
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
    except:
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


def get_git_hash():
    screenly_path = os.path.join(os.getenv('HOME'), 'screenly', '.git')
    try:
        get_hash = sh.git(
            '--git-dir={}'.format(screenly_path),
            'rev-parse',
            'HEAD'
        )
        return get_hash.stdout.strip()
    except:
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
