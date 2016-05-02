#!/usr/bin/env python

import netifaces
import os
import sh
import socket
import sqlite3
import utils
from pprint import pprint
from uptime import uptime


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
    for line in sh.lsmod():
        if 'Module' not in line:
            modules.append(line.split()[0])
    return modules


def get_monitor_status():
    try:
        return sh.tvservice('-s').stdout.strip()
    except:
        return 'Unable to run tvservice.'


def get_network_interfaces():
    if_data = {}
    for interface in netifaces.interfaces():
        if_data[interface] = netifaces.ifaddresses(interface)
    return if_data


def get_uptime():
    return uptime()


def get_playlist():
    screenly_db = '/home/pi/.screenly/screenly.db'
    playlist = []
    if os.path.isfile(screenly_db):
        conn = sqlite3.connect(screenly_db)
        c = conn.cursor()
        for row in c.execute('SELECT * FROM assets;'):
            playlist.append(row)
        c.close
    return playlist


def get_git_hash():
    screenly_path = '/home/pi/screenly'
    get_hash = sh.git('-C', screenly_path, 'rev-parse', 'HEAD')
    return get_hash.stdout.strip()


def try_connectivity():
    urls = ['http://www.google.com', 'http://www.bbc.co.uk']
    result = []
    for url in urls:
        if utils.url_fails(url):
            result.append('{}: Error'.format(url))
        else:
            result.append('{}: OK'.format(url))
    return result


def compile_report():
    report = {}
    report['cpu_info'] = parse_cpu_info()
    report['uptime'] = get_uptime()
    report['kernel_modules'] = get_kernel_modules()
    report['monitor'] = get_monitor_status()
    report['ifconfig'] = get_network_interfaces()
    report['hostname'] = socket.gethostname()
    report['playlist'] = get_playlist()
    report['git_hash'] = get_git_hash()
    report['connectivity'] = try_connectivity()

    return report


def __main__():
    pprint(compile_report())

__main__()
