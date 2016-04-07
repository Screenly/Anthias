import netifaces
import time


def is_interface_up(interface):
    addr = netifaces.ifaddresses(interface)
    return netifaces.AF_INET in addr


for _ in range(1, 30):
    if is_interface_up('eth0'):
        break;
    print('wait for eth0 up')
    time.sleep(1)
