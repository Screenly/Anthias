import json
from netifaces import ifaddresses


def handler(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        raise TypeError('Object of type %s with value of %s is not JSON serializable' % (type(obj), repr(obj)))


def json_dump(obj):
    return json.dumps(obj, default=handler)


def get_node_ip():
    """Returns this node's IP, if it can be
    determined, returning None if not."""

    precedence = ["eth0", "eth1", "en0", "en1"]

    for interface in precedence:
        try:
            my_ip = ifaddresses(interface)[2][0]['addr']
            return my_ip
        except:
            pass

    return None
