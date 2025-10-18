import threading

# Global event for instant asset switching
skip_event = threading.Event()


def skip_asset(scheduler, back=False):
    if back is True:
        scheduler.reverse = True
    skip_event.set()


def navigate_to_asset(scheduler, asset_id):
    scheduler.extra_asset = asset_id
    skip_event.set()


def stop_loop(scheduler):
    skip_asset(scheduler)
    return True


def play_loop():
    return False
