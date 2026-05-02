import json
import logging
import os
import random
import re
import string
from datetime import datetime, timedelta
from os import getenv, path, utime
from platform import machine
from subprocess import call, check_output
from threading import Thread
from time import sleep
from typing import Any
from urllib.parse import urlparse

import certifi
import pytz
import redis
import requests
import sh
from tenacity import (
    RetryError,
    Retrying,
    stop_after_attempt,
    wait_fixed,
)

from anthias_app.models import Asset
from settings import settings

arch = machine()


def string_to_bool(string: Any) -> bool:
    # Direct port of distutils.util.strtobool (removed in Python 3.12)
    # so existing callers keep accepting the same y/yes/t/true/on/1 set.
    value = str(string).strip().lower()
    if value in ('y', 'yes', 't', 'true', 'on', '1'):
        return True
    if value in ('n', 'no', 'f', 'false', 'off', '0'):
        return False
    raise ValueError(f'invalid truth value {string!r}')


def touch(path: str) -> None:
    with open(path, 'a'):
        utime(path, None)


def is_ci() -> bool:
    """
    Returns True when run on CI.
    """
    return string_to_bool(os.getenv('CI', False))


def validate_url(string: str) -> bool:
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


def get_balena_supervisor_api_response(
    method: str,
    action: str,
    **kwargs: Any,
) -> requests.Response:
    version = kwargs.get('version', 'v1')
    response: requests.Response = getattr(requests, method)(
        '{}/{}/{}?apikey={}'.format(
            os.getenv('BALENA_SUPERVISOR_ADDRESS'),
            version,
            action,
            os.getenv('BALENA_SUPERVISOR_API_KEY'),
        ),
        headers={'Content-Type': 'application/json'},
    )
    return response


def get_balena_device_info() -> requests.Response:
    return get_balena_supervisor_api_response(method='get', action='device')


def shutdown_via_balena_supervisor() -> requests.Response:
    return get_balena_supervisor_api_response(method='post', action='shutdown')


def reboot_via_balena_supervisor() -> requests.Response:
    return get_balena_supervisor_api_response(method='post', action='reboot')


def get_balena_supervisor_version() -> str:
    response = get_balena_supervisor_api_response(
        method='get', action='version', version='v2'
    )
    if response.ok:
        return str(response.json()['version'])
    else:
        return 'Error getting the Supervisor version'


def get_node_ip() -> str:
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
            return str(response.json()['ip_address'])
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
            return os.getenv('MY_IP') or 'Unable to retrieve IP.'

    return 'Unable to retrieve IP.'


def get_node_mac_address() -> str:
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
            return str(r.json()['mac_address'])
        return 'Unknown'

    return os.getenv('MAC_ADDRESS', 'Unable to retrieve MAC address.')


def get_active_connections(
    bus: Any,
    fields: list[str] | None = None,
) -> list[dict[str, Any]] | None:
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


def remove_connection(bus: Any, uuid: str) -> bool:
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


def get_video_duration(file: str) -> timedelta | None:
    """
    Returns the duration of a video file in timedelta.

    Returns None if ffprobe is not available on the host so callers can
    surface a clean validation error instead of a 500.
    """
    time = None

    try:
        run_player = sh.Command('ffprobe')('-i', file, _err_to_out=True)
    except sh.CommandNotFound:
        logging.warning('ffprobe is not installed; cannot determine duration')
        return None
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


def handler(obj: Any) -> str:
    # Set timezone as UTC if it's datetime and format as ISO
    if isinstance(obj, datetime):
        with_tz = obj.replace(tzinfo=pytz.utc)
        return with_tz.isoformat()
    else:
        raise TypeError(
            f'Object of type {type(obj)} with value of {repr(obj)} '
            'is not JSON serializable'
        )


def json_dump(obj: Any) -> str:
    return json.dumps(obj, default=handler)


def url_fails(url: str) -> bool:
    """
    If it is streaming
    """
    if urlparse(url).scheme in ('rtsp', 'rtmp'):
        # ffprobe ships with ffmpeg, which is already in the base
        # image. Exit code 0 means libavformat could open the stream
        # and read its header; non-zero means it could not. The
        # 15s wall-clock cap mirrors the implicit cap mplayer gave us
        # via `-frames 0` (it would tear down once it had probed the
        # stream) and prevents a stuck RTSP handshake from hanging
        # the API request that called us.
        try:
            sh.Command('ffprobe')(
                '-v',
                'quiet',
                '-show_streams',
                '-i',
                url,
                _timeout=15,
            )
        except sh.CommandNotFound:
            logging.warning(
                'ffprobe is not installed; skipping streaming URL probe'
            )
            return False
        except (sh.TimeoutException, sh.ErrorReturnCode):
            return True
        return False

    """
    Try HEAD and GET for URL availability check.
    """

    # Use Certifi module and set to True as default so users stop
    # seeing InsecureRequestWarning in logs.
    verify: str | bool
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


def download_video_from_youtube(
    uri: str,
    asset_id: str,
) -> tuple[str, str, int]:
    name = check_output(['yt-dlp', '-O', 'title', uri])
    info = json.loads(check_output(['yt-dlp', '-j', uri]))
    duration = info['duration']

    # Write into settings['assetdir'] so cleanup() (which sweeps the same
    # path) sees these files; otherwise a custom assetdir would leak
    # orphaned YouTube downloads in $HOME/anthias_assets.
    location = path.join(settings['assetdir'], f'{asset_id}.mp4')
    thread = YoutubeDownloadThread(location, uri, asset_id)
    thread.daemon = True
    thread.start()

    return location, str(name.decode('utf-8')), duration


class YoutubeDownloadThread(Thread):
    def __init__(
        self,
        location: str,
        uri: str,
        asset_id: str,
    ) -> None:
        Thread.__init__(self)
        self.location = location
        self.uri = uri
        self.asset_id = asset_id

    def run(self) -> None:
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
            asset.is_processing = False
            asset.save()
        except Asset.DoesNotExist:
            logging.warning('Asset %s not found', self.asset_id)
            return

        # Imported lazily so the viewer container (which does not
        # ship channels/channels-redis) can still import lib.utils.
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        async_to_sync(get_channel_layer().group_send)(
            'ws_server',
            {'type': 'asset.update', 'asset_id': self.asset_id},
        )


def template_handle_unicode(value: Any) -> str:
    return str(value)


def is_demo_node() -> bool:
    """
    Check if the environment variable IS_DEMO_NODE is set to 1
    :return: bool
    """
    return string_to_bool(os.getenv('IS_DEMO_NODE', False))


def generate_perfect_paper_password(
    pw_length: int = 10,
    has_symbols: bool = True,
) -> str:
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


def connect_to_redis() -> 'redis.Redis':
    return redis.Redis(host='redis', decode_responses=True, port=6379, db=0)


def is_docker() -> bool:
    return os.path.isfile('/.dockerenv')


def is_balena_app() -> bool:
    """
    Checks the application is running on Balena Cloud
    :return: bool
    """
    return bool(getenv('BALENA', False))
