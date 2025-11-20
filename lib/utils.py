from __future__ import absolute_import, unicode_literals

import json
import logging
import os
import random
import re
import string
from builtins import range, str
from datetime import datetime, timedelta
from distutils.util import strtobool
from os import getenv, path, utime
from platform import machine
from subprocess import call, check_output
from threading import Thread
from time import sleep
from urllib.parse import urlparse

import certifi
import pytz
import redis
import requests
import sh
from future import standard_library
from tenacity import (
    RetryError,
    Retrying,
    stop_after_attempt,
    wait_fixed,
)

from anthias_app.models import Asset
from settings import ZmqPublisher, settings

standard_library.install_aliases()


arch = machine()

# This will only work on the Raspberry Pi,
# so let's wrap it in a try/except so that
# Travis can run.
try:
    from sh import ffprobe
except ImportError:
    pass


def string_to_bool(string):
    return bool(strtobool(str(string)))


def touch(path):
    with open(path, 'a'):
        utime(path, None)


def is_ci():
    """
    Returns True when run on CI.
    """
    return string_to_bool(os.getenv('CI', False))


def validate_url(string):
    """Simple URL verification.
    >>> validate_url("hello")
    False
    >>> validate_url("ftp://example.com")
    False
    >>> validate_url("http://")
    False
    >>> validate_url("http://wireload.net/logo.png")
    True
    >>> validate_url("https://wireload.net/logo.png")
    True
    """

    checker = urlparse(string)
    return bool(
        checker.scheme in ('http', 'https', 'rtsp', 'rtmp') and checker.netloc
    )


def get_balena_supervisor_api_response(method, action, **kwargs):
    version = kwargs.get('version', 'v1')
    return getattr(requests, method)(
        '{}/{}/{}?apikey={}'.format(
            os.getenv('BALENA_SUPERVISOR_ADDRESS'),
            version,
            action,
            os.getenv('BALENA_SUPERVISOR_API_KEY'),
        ),
        headers={'Content-Type': 'application/json'},
    )


def get_balena_device_info():
    return get_balena_supervisor_api_response(method='get', action='device')


def shutdown_via_balena_supervisor():
    return get_balena_supervisor_api_response(method='post', action='shutdown')


def reboot_via_balena_supervisor():
    return get_balena_supervisor_api_response(method='post', action='reboot')


def get_balena_supervisor_version():
    response = get_balena_supervisor_api_response(
        method='get', action='version', version='v2'
    )
    if response.ok:
        return response.json()['version']
    else:
        return 'Error getting the Supervisor version'


def get_node_ip():
    """
    Returns the node's IP address.
    We're using an API call to the supervisor for this on Balena
    and an environment variable set by `install.sh` for other environments.
    The reason for this is because we can't retrieve the host IP from
    within Docker.
    """

    if is_balena_app():
        response = get_balena_device_info()
        if response.ok:
            return response.json()['ip_address']
        return 'Unknown'
    else:
        r = connect_to_redis()
        max_retries = 60
        retries = 0

        while True:
            environment = getenv('ENVIRONMENT', None)
            if environment in ['development', 'test']:
                break

            is_ready = r.get('host_agent_ready') or 'false'

            if json.loads(is_ready):
                break

            if retries >= max_retries:
                logging.info(
                    'host_agent_service is not ready after %d retries',
                    max_retries,
                )
                break

            retries += 1
            sleep(1)

        r.publish('hostcmd', 'set_ip_addresses')

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(20),
                wait=wait_fixed(1),
            ):
                environment = getenv('ENVIRONMENT', None)
                if environment in ['development', 'test']:
                    break

                with attempt:
                    ip_addresses_ready = r.get('ip_addresses_ready') or 'false'
                    if json.loads(ip_addresses_ready):
                        break
                    else:
                        raise Exception(
                            'Internet connection is not available.'
                        )
        except RetryError:
            logging.warning('Internet connection is not available. ')

        ip_addresses = r.get('ip_addresses')

        if ip_addresses:
            return ' '.join(json.loads(ip_addresses))
        elif os.getenv('MY_IP'):
            return os.getenv('MY_IP')

    return 'Unable to retrieve IP.'


def get_node_mac_address():
    """
    Returns the MAC address.
    """
    if is_balena_app():
        balena_supervisor_address = os.getenv('BALENA_SUPERVISOR_ADDRESS')
        balena_supervisor_api_key = os.getenv('BALENA_SUPERVISOR_API_KEY')
        headers = {'Content-Type': 'application/json'}

        r = requests.get(
            '{}/v1/device?apikey={}'.format(
                balena_supervisor_address, balena_supervisor_api_key
            ),
            headers=headers,
        )

        if r.ok:
            return r.json()['mac_address']
        return 'Unknown'

    return os.getenv('MAC_ADDRESS', 'Unable to retrieve MAC address.')


def get_active_connections(bus, fields=None):
    """

    :param bus: pydbus.bus.Bus
    :param fields: list
    :return: list
    """
    if not fields:
        fields = ['Id', 'Uuid', 'Type', 'Devices']

    connections = list()

    try:
        nm_proxy = bus.get(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
        )
    except Exception:
        return None

    nm_properties = nm_proxy['org.freedesktop.DBus.Properties']
    active_connections = nm_properties.Get(
        'org.freedesktop.NetworkManager', 'ActiveConnections'
    )
    for active_connection in active_connections:
        active_connection_proxy = bus.get(
            'org.freedesktop.NetworkManager', active_connection
        )
        active_connection_properties = active_connection_proxy[
            'org.freedesktop.DBus.Properties'
        ]

        connection = dict()
        for field in fields:
            field_value = active_connection_properties.Get(
                'org.freedesktop.NetworkManager.Connection.Active', field
            )

            if field == 'Devices':
                devices = list()
                for device_path in field_value:
                    device_proxy = bus.get(
                        'org.freedesktop.NetworkManager', device_path
                    )
                    device_properties = device_proxy[
                        'org.freedesktop.DBus.Properties'
                    ]
                    devices.append(
                        device_properties.Get(
                            'org.freedesktop.NetworkManager.Device',
                            'Interface',
                        )
                    )
                field_value = devices

            connection.update({field: field_value})
        connections.append(connection)

    return connections


def remove_connection(bus, uuid):
    """

    :param bus: pydbus.bus.Bus
    :param uuid: string
    :return: boolean
    """
    try:
        nm_proxy = bus.get(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager/Settings',
        )
    except Exception:
        return False

    nm_settings = nm_proxy['org.freedesktop.NetworkManager.Settings']

    connection_path = nm_settings.GetConnectionByUuid(uuid)
    connection_proxy = bus.get(
        'org.freedesktop.NetworkManager', connection_path
    )
    connection = connection_proxy[
        'org.freedesktop.NetworkManager.Settings.Connection'
    ]
    connection.Delete()

    return True


def get_video_duration(file):
    """
    Returns the duration of a video file in timedelta.
    """
    time = None

    try:
        run_player = ffprobe('-i', file, _err_to_out=True)
    except sh.ErrorReturnCode_1 as err:
        raise Exception('Bad video format') from err

    for line in run_player.split('\n'):
        if 'Duration' in line:
            match = re.search(r'[0-9]+:[0-9]+:[0-9]+\.[0-9]+', line)
            if match:
                time_input = match.group()
                time_split = time_input.split(':')
                hours = int(time_split[0])
                minutes = int(time_split[1])
                seconds = float(time_split[2])
                time = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            break

    return time


def handler(obj):
    # Set timezone as UTC if it's datetime and format as ISO
    if isinstance(obj, datetime):
        with_tz = obj.replace(tzinfo=pytz.utc)
        return with_tz.isoformat()
    else:
        raise TypeError(
            f'Object of type {type(obj)} with value of {repr(obj)} '
            'is not JSON serializable'
        )


def json_dump(obj):
    return json.dumps(obj, default=handler)


def url_fails(url):
    """
    If it is streaming
    """
    if urlparse(url).scheme in ('rtsp', 'rtmp'):
        run_mplayer = mplayer(  # noqa: F821
            '-identify', '-frames', '0', '-nosound', url
        )
        for line in run_mplayer.split('\n'):
            if 'Clip info:' in line:
                return False
        return True

    """
    Try HEAD and GET for URL availability check.
    """

    # Use Certifi module and set to True as default so users stop
    # seeing InsecureRequestWarning in logs.
    if settings['verify_ssl']:
        verify = certifi.where()
    else:
        verify = True

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/538.15 (KHTML, like Gecko) Version/8.0 Safari/538.15'  # noqa: E501
    }
    try:
        if not validate_url(url):
            return False

        if requests.head(
            url,
            allow_redirects=True,
            headers=headers,
            timeout=10,
            verify=verify,
        ).ok:
            return False

        if requests.get(
            url,
            allow_redirects=True,
            headers=headers,
            timeout=10,
            verify=verify,
        ).ok:
            return False

    except (requests.ConnectionError, requests.exceptions.Timeout):
        pass

    return True


def download_video_from_youtube(uri, asset_id):
    home = getenv('HOME')
    name = check_output(['yt-dlp', '-O', 'title', uri])
    info = json.loads(check_output(['yt-dlp', '-j', uri]))
    duration = info['duration']

    location = path.join(home, 'screenly_assets', f'{asset_id}.mp4')
    thread = YoutubeDownloadThread(location, uri, asset_id)
    thread.daemon = True
    thread.start()

    return location, str(name.decode('utf-8')), duration


class YoutubeDownloadThread(Thread):
    def __init__(self, location, uri, asset_id):
        Thread.__init__(self)
        self.location = location
        self.uri = uri
        self.asset_id = asset_id

    def run(self):
        publisher = ZmqPublisher.get_instance()
        call(
            [
                'yt-dlp',
                '-S',
                'vcodec:h264,fps,res:1080,acodec:m4a',
                '-o',
                self.location,
                self.uri,
            ]
        )

        try:
            asset = Asset.objects.get(asset_id=self.asset_id)
            asset.is_processing = 0
            asset.save()
        except Asset.DoesNotExist:
            logging.warning('Asset %s not found', self.asset_id)
            return

        publisher.send_to_ws_server(self.asset_id)


def template_handle_unicode(value):
    return str(value)


def is_demo_node():
    """
    Check if the environment variable IS_DEMO_NODE is set to 1
    :return: bool
    """
    return string_to_bool(os.getenv('IS_DEMO_NODE', False))


def generate_perfect_paper_password(pw_length=10, has_symbols=True):
    """
    Generates a password using 64 characters from
    "Perfect Paper Password" system by Steve Gibson

    :param pw_length: int
    :param has_symbols: bool
    :return: string
    """
    ppp_letters = (
        '!#%+23456789:=?@ABCDEFGHJKLMNPRSTUVWXYZabcdefghjkmnopqrstuvwxyz'  # noqa: E501
    )
    if not has_symbols:
        ppp_letters = ''.join(set(ppp_letters) - set(string.punctuation))
    return ''.join(
        random.SystemRandom().choice(ppp_letters) for _ in range(pw_length)
    )


def connect_to_redis():
    return redis.Redis(host='redis', decode_responses=True, port=6379, db=0)


def is_docker():
    return os.path.isfile('/.dockerenv')


def is_balena_app():
    """
    Checks the application is running on Balena Cloud
    :return: bool
    """
    return bool(getenv('BALENA', False))
