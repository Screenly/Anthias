import requests
import json
from netifaces import ifaddresses
from sh import grep, netstat
from urlparse import urlparse


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
