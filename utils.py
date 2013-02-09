import json
from netifaces import ifaddresses
from sh import grep, netstat

def handler(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        raise TypeError('Object of type %s with value of %s is not JSON serializable' % (type(obj), repr(obj)))


def json_dump(obj):
    return json.dumps(obj, default=handler)


def get_node_ip():
    """Returns the node's IP, for the interface
    that is being used as the default gateway.
    This shuld work on both MacOS X and Linux."""

    try:
        default_interface = grep(netstat('-nr'),'-e', '^default', '-e' '^0.0.0.0').split()[-1]
        my_ip = ifaddresses(default_interface)[2][0]['addr']
        return my_ip
    except:
        pass

    return None
