import threading
from typing import Any

# Global event for instant asset switching
skip_event = threading.Event()


def skip_asset(scheduler: Any, back: bool = False) -> None:
    if back is True:
        scheduler.reverse = True
    skip_event.set()


def navigate_to_asset(scheduler: Any, asset_id: str) -> None:
    scheduler.extra_asset = asset_id
    skip_event.set()


def stop_loop(scheduler: Any) -> bool:
    skip_asset(scheduler)
    return True


def play_loop() -> bool:
    return False
