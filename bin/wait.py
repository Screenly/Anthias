import time

import sh


# Wait for a default route. `ip route` ships in iproute2 (already in
# the base debian:trixie image); the previous implementation used
# `route` from net-tools, which was dropped from the runtime apt set.
def is_routing_up() -> bool:
    try:
        sh.grep('default', _in=sh.ip('route'))
        return True
    except sh.ErrorReturnCode_1:
        return False


for _ in range(1, 30):
    if is_routing_up():
        break
    print('Waiting for to come up...')
    time.sleep(1)
