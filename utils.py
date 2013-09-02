import requests
import json
import re
from netifaces import ifaddresses
from sh import grep, netstat, omxplayer
from urlparse import urlparse
from datetime import timedelta


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
    return time


def handler(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        raise TypeError('Object of type %s with value of %s is not JSON serializable' % (type(obj), repr(obj)))


def json_dump(obj):
    return json.dumps(obj, default=handler)


def url_fails(url):
    try:
        if validate_url(url):
            obj = requests.head(url, allow_redirects=True)
            assert obj.status_code == 200
    except (requests.ConnectionError, AssertionError):
        return True
    else:
        return False
