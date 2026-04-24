from __future__ import print_function, unicode_literals

import time
from builtins import range

import sh


# wait for default route
def is_routing_up():
    try:
        sh.grep('default', _in=sh.route())
        return True
    except sh.ErrorReturnCode_1:
        return False


for _ in range(1, 30):
    if is_routing_up():
        break
    print('Waiting for to come up...')
    time.sleep(1)
