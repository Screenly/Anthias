from netifaces import ifaddresses


def get_node_ip():
    """Returns this node's IP, if it can be
    determined, returning None if not."""

    precedence = ["eth0", "eth1", "en0", "en1", "wlan0", "wlan1"]

    for interface in precedence:
        try:
            my_ip = ifaddresses(interface)[2][0]['addr']
            return my_ip
        except:
            pass

    return None
