#!/usr/bin/env python
import configparser
import netifaces
import os
import re
import requests
import sh
import socket
import sys
import syslog
import time

NETWORK_PATH = '/boot/network.ini'
LOCKFILE = '/tmp/net_watchdog.lock'


def get_default_gw():
    gws = netifaces.gateways()
    return gws['default'][netifaces.AF_INET][0]


def ping_test(host):
    ping = sh.ping('-q', '-c', 10, host, _ok_code=[0, 1])
    packet_loss = re.findall(r'(\d+)% packet loss', ping.stdout)

    if int(packet_loss) > 60:
        syslog.syslog(syslog.LOG_ERR, 'Unable to ping gateway.')
        return False
    else:
        return True


def http_test(host):
    r = requests.head(host, allow_redirects=True)
    if 200 <= r.status_code < 400:
        return True
    else:
        syslog.syslog(syslog.LOG_ERR, 'Unable to reach Screenly.')
        return False


def restart_interface(interface):
    syslog.syslog('Restarting network interface.')

    ifdown = sh.Command('/sbin/ifdown')
    ifdown('--force', interface)

    networking = sh.Command('/etc/init.d/networking')
    networking('restart')


def is_static(config, interface):
    ip = config.get(interface, 'ip', fallback=False)
    netmask = config.get(interface, 'netmask', fallback=False)
    gateway = config.get(interface, 'gateway', fallback=False)
    return ip and netmask and gateway


def get_lock():
    if os.path.isfile(LOCKFILE):

        # Stale lock file
        stale_timestamp = time.time() - 60 * 30
        if os.stat(LOCKFILE).st_mtime < stale_timestamp:
            os.remove(LOCKFILE)
        else:
            return False

    with open(LOCKFILE, 'w') as f:
        f.write(os.getpid())
        return True


def release_lock():
    os.remove(LOCKFILE)
    return True


def bring_up_interface(interface):
    retry_limit = 10
    retries = 0
    while retries < retry_limit:
        restart_interface(interface)
        if has_ip(interface):
            return True
        else:
            retries += 1
            time.sleep(15)
    syslog.syslog(syslog.LOG_ERR, 'Unable to bring up network interface.')
    return False


def has_ip(interface):
    """
    Return True if interface has an IP.
    """
    try:
        ips = netifaces.ifaddresses(interface)
    except ValueError:
        syslog.syslog(syslog.LOG_ERR, 'Interface does not exist.')
        return False
    for k in ips.keys():
        ip = ips[k][0].get('addr', False)
        if ip:
            try:
                socket.inet_aton(ip)
                return True
            except socket.error:
                pass
    return False

if __name__ == '__main__':

    syslog.syslog('Starting net_watchdog.')

    if not get_lock():
        syslog.syslog('Lockfile exist. Exiting.')
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(NETWORK_PATH)

    wifi = config.has_section('wlan0')
    can_ping_gw = None
    reaches_internet = None

    if wifi:
        wifi_is_static = is_static(config, 'wlan0')
        wifi_is_healthy = None

        if not wifi_is_static:
            wifi_has_ip = has_ip('wlan0')
        else:
            wifi_has_ip = True

        # Preliminarily assume interface is healthy if it has an IP.
        wifi_is_healthy = wifi_has_ip

        if not wifi_is_healthy:
            wifi_is_healthy = bring_up_interface('wlan0')

        if wifi_is_healthy:
            reaches_internet = http_test('http://www.screenlyapp.com')
            can_ping_gw = ping_test(get_default_gw())

        if reaches_internet or can_ping_gw:
            syslog.syslog('WiFi interface is healthy.')
        else:
            syslog.syslog(
                syslog.LOG_ERR,
                'Unable to connect to internet or gateway.'
            )

    release_lock()
