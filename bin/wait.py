import time
import sh


# wait for default route
def is_routing_up():
    try:
        sh.grep(sh.route(), 'default')
        return True
    except sh.ErrorReturnCode_1:
        return False


for _ in range(1, 30):
    if is_routing_up():
        break
    print('Waiting for to come up...')
    time.sleep(1)
