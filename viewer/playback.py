from os import system


def skip_asset(scheduler, back=False):
    if back is True:
        scheduler.reverse = True
    system('pkill -SIGUSR1 -f viewer')


def navigate_to_asset(scheduler, asset_id):
    scheduler.extra_asset = asset_id
    system('pkill -SIGUSR1 -f viewer')


def stop_loop(scheduler):
    skip_asset(scheduler)
    return True


def play_loop():
    return False
