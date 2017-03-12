import requests
import json
import re
import certifi
from netifaces import ifaddresses
from sh import grep, netstat
from urlparse import urlparse
from datetime import timedelta
from settings import settings
from datetime import datetime
import pytz
from platform import machine

arch = machine()

HTTP_OK = xrange(200, 299)

# This will only work on the Raspberry Pi,
# so let's wrap it in a try/except so that
# Travis can run.
try:
    from sh import omxplayer
except:
    pass

# This will work on x86-based machines
if machine() in ['x86', 'x86_64']:
    try:
        from sh import mplayer
    except:
        pass


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
    return bool(checker.scheme in ('http', 'https') and checker.netloc)


def get_node_ip():
    """Returns the node's IP, for the interface
    that is being used as the default gateway.
    This shuld work on both MacOS X and Linux."""

    try:
        default_interface = grep(netstat('-nr'), '-e', '^default', '-e' '^0.0.0.0').split()[-1]
        my_ip = ifaddresses(default_interface)[2][0]['addr']
        return my_ip
    except:
        pass

    return None


def get_video_duration(file):
    """
    Returns the duration of a video file in timedelta.
    """
    time = None
    try:
        if arch in ('armv6l', 'armv7l'):
            run_omxplayer = omxplayer(file, info=True, _err_to_out=True)
            for line in run_omxplayer.split('\n'):
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
        else:
            run_mplayer = mplayer('-identify', '-frames', '0', '-nosound', file)
            for line in run_mplayer.split('\n'):
                if 'ID_LENGTH=' in line:
                    time = timedelta(seconds=int(round(float(line.split('=')[1]))))
                    break
    except:
        pass

    return time


def handler(obj):
    # Set timezone as UTC if it's datetime and format as ISO
    if isinstance(obj, datetime):
        with_tz = obj.replace(tzinfo=pytz.utc)
        return with_tz.isoformat()
    else:
        raise TypeError('Object of type %s with value of %s is not JSON serializable' % (type(obj), repr(obj)))


def json_dump(obj):
    return json.dumps(obj, default=handler)


def url_fails(url):
    """
    Try HEAD and GET for URL availability check.
    """

    # Use Certifi module
    if settings['verify_ssl']:
        verify = certifi.where()
    else:
        verify = False

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/538.15 (KHTML, like Gecko) Version/8.0 Safari/538.15',
    }
    try:
        if not validate_url(url):
            return False

        if requests.head(
            url,
            allow_redirects=True,
            headers=headers,
            timeout=10,
            verify=verify
        ).status_code in HTTP_OK:
            return False

        if requests.get(
            url,
            allow_redirects=True,
            headers=headers,
            timeout=10,
            verify=verify
        ).status_code in HTTP_OK:
            return False

    except (requests.ConnectionError, requests.exceptions.Timeout):
        pass

    return True


def template_handle_unicode(value):
    if isinstance(value, str):
        return value.decode('utf-8')
    return unicode(value)
