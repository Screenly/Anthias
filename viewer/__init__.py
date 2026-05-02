# -*- coding: utf-8 -*-

import logging
import sys
from os import getenv, path
from signal import SIGALRM, signal
from time import monotonic, sleep
from typing import Any

import django
import pydbus
import requests
import sh as sh

from settings import LISTEN, PORT, ReplySender, settings
from viewer.constants import EMPTY_PL_DELAY as EMPTY_PL_DELAY
from viewer.constants import SERVER_WAIT_TIMEOUT as SERVER_WAIT_TIMEOUT
from viewer.constants import SPLASH_DELAY as SPLASH_DELAY
from viewer.constants import SPLASH_PAGE_URL as SPLASH_PAGE_URL
from viewer.constants import STANDBY_SCREEN as STANDBY_SCREEN
from viewer.media_player import MediaPlayerProxy
from viewer.playback import navigate_to_asset, play_loop, skip_asset, stop_loop
from viewer.utils import (
    command_not_found,
    get_skip_event,
    sigalrm,
    wait_for_server,
    watchdog,
)

django.setup()

# Place imports that uses Django in this block.

from lib.utils import (  # noqa: E402
    connect_to_redis,
    string_to_bool,
)
from viewer.messaging import ViewerSubscriber  # noqa: E402
from viewer.scheduling import Scheduler  # noqa: E402


__author__ = 'Screenly, Inc'
__copyright__ = 'Copyright 2012-2026, Screenly, Inc'
__license__ = 'Dual License: GPLv2 and Commercial License'


current_browser_url: str | None = None
browser: Any = None
loop_is_stopped: bool = False
browser_bus: Any = None
r = connect_to_redis()
reply_sender = ReplySender(r)

HOME: str | None = None

scheduler: Any = None


def send_current_asset_id_to_server(correlation_id: str | None) -> None:
    if not correlation_id:
        logging.warning(
            'current_asset_id command received without a correlation ID; '
            'dropping reply.'
        )
        return

    # `subscriber.start()` runs before `scheduler = Scheduler()` in
    # main(), so a `current_asset_id` command arriving during the
    # `wait_for_server` window would `AttributeError` on
    # `scheduler.current_asset_id`. Reply with `None` instead — the v1
    # endpoint already treats a falsy id as "no current asset" and
    # returns `[]`, which is the correct answer pre-scheduler-init.
    if scheduler is None:
        logging.info(
            'current_asset_id requested before scheduler was ready; '
            'replying with no current asset.'
        )
        reply_sender.send(correlation_id, {'current_asset_id': None})
        return

    reply_sender.send(
        correlation_id, {'current_asset_id': scheduler.current_asset_id}
    )


commands = {
    'next': lambda _: skip_asset(scheduler),
    'previous': lambda _: skip_asset(scheduler, back=True),
    'asset': lambda asset_id: navigate_to_asset(scheduler, asset_id),
    'reload': lambda _: load_settings(),
    'stop': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', stop_loop(scheduler)
    ),
    'play': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', play_loop()
    ),
    'unknown': lambda _: command_not_found(),
    'current_asset_id': lambda corr: send_current_asset_id_to_server(corr),
}


BROWSER_STARTUP_TIMEOUT_SECONDS = 30
BROWSER_HANDSHAKE_LINE = 'Anthias service start'


def load_browser() -> None:
    global browser
    logging.info('Loading browser...')

    browser = sh.Command('AnthiasWebview')(_bg=True, _err_to_out=True)

    # Bound the wait so we don't hang the viewer indefinitely if
    # AnthiasWebview fails to register on D-Bus (missing binary, broken
    # library link, handshake-line drift, etc.). The string here must
    # match `qInfo() << "Anthias service start"` in webview/src/main.cpp.
    deadline = monotonic() + BROWSER_STARTUP_TIMEOUT_SECONDS
    while monotonic() < deadline:
        if BROWSER_HANDSHAKE_LINE in browser.process.stdout.decode('utf-8'):
            return
        if not browser.is_alive():
            raise RuntimeError(
                'AnthiasWebview exited before emitting D-Bus handshake; '
                'stdout: '
                + browser.process.stdout.decode('utf-8', errors='replace')
            )
        sleep(1)

    raise TimeoutError(
        f'AnthiasWebview did not emit "{BROWSER_HANDSHAKE_LINE}" within '
        f'{BROWSER_STARTUP_TIMEOUT_SECONDS}s'
    )


def view_webpage(uri: str) -> None:
    global current_browser_url

    if browser is None or not browser.is_alive():
        load_browser()
    if current_browser_url is not uri:
        browser_bus.loadPage(uri)
        current_browser_url = uri
    logging.info('Current url is {0}'.format(current_browser_url))


def view_image(uri: str) -> None:
    global current_browser_url

    if browser is None or not browser.is_alive():
        load_browser()
    if current_browser_url is not uri:
        browser_bus.loadImage(uri)
        current_browser_url = uri
    logging.info('Current url is {0}'.format(current_browser_url))

    if string_to_bool(getenv('WEBVIEW_DEBUG', '0')):
        logging.info(browser.process.stdout)


def view_video(uri: str, duration: int | str) -> None:
    logging.debug('Displaying video %s for %s ', uri, duration)
    media_player = MediaPlayerProxy.get_instance()

    media_player.set_asset(uri, duration)
    media_player.play()

    view_image('null')

    try:
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=int(duration)):
            logging.info('Skip detected during video playback, stopping video')
            media_player.stop()
        else:
            pass
    except sh.ErrorReturnCode_1:
        logging.info(
            'Resource URI is not correct, remote host is not responding or '
            'request was rejected.'
        )

    media_player.stop()


def load_settings() -> None:
    """
    Load settings and set the log level.
    """
    settings.load()
    logging.getLogger().setLevel(
        logging.DEBUG if settings['debug_logging'] else logging.INFO
    )


def _asset_is_displayable(asset: dict[str, Any]) -> bool:
    """Decide whether to play an asset this rotation.

    The reachability of remote URLs is owned by the server (a celery
    beat task refreshes ``Asset.is_reachable`` on a 15-min cadence and
    the ``/api/v2/assets/<id>/recheck`` endpoint covers on-demand
    re-validation). The viewer used to call ``url_fails`` itself on
    every play, but ffprobe on streams blocks the loop for up to 15s
    per rotation — so we trust the field instead and let the server
    own that work.

    Local files still get a filesystem check: the asset row's
    ``is_reachable`` is set against the celery worker's view of the
    filesystem, but assetdir is shared by volume so the answer is the
    same. Cheap, no roundtrip, mirrors prior behavior for local files.
    """
    if asset.get('skip_asset_check'):
        return True
    uri = asset.get('uri') or ''
    if uri.startswith('/'):
        return path.isfile(uri)
    # Default to True so a row written before this field existed
    # (or by an older serializer that doesn't set it) doesn't get
    # silently skipped.
    return bool(asset.get('is_reachable', True))


def _trigger_asset_recheck(asset_id: str | None) -> None:
    """Ask the server to re-probe an asset we couldn't display.

    Best-effort: a failure here just means the asset stays marked
    unreachable until the next periodic sweep, which is acceptable.
    The server-side task rate-limits per asset, so spamming this on
    every rotation through an unreachable asset is safe.
    """
    if not asset_id:
        return
    try:
        # NOSONAR (S5332): viewer talks to anthias-server over plain
        # HTTP per CLAUDE.md (TLS is opt-in via the Caddy sidecar that
        # bin/enable_ssl.sh installs as a compose override). The
        # production compose templates set LISTEN=anthias-server in the
        # viewer container's environment, so this resolves to the
        # in-stack service hostname; the settings.py default of
        # 127.0.0.1 only kicks in for non-compose deployments. Either
        # way the URL never crosses a network boundary on a default
        # deploy.
        response = requests.post(
            f'http://{LISTEN}:{PORT}/api/v2/assets/{asset_id}/recheck',  # NOSONAR
            timeout=2,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        logging.debug('Failed to trigger recheck for %s: %s', asset_id, e)
        return

    if response.status_code != 202:
        # 404 means the row was deleted between scheduler refresh and
        # this call — the recheck is moot. Anything else (a 5xx, or a
        # 401/302 if the endpoint ever gets re-decorated with @authorized)
        # means the recheck didn't actually enqueue. Log at debug so the
        # operator can see the chain is silently broken without spamming
        # the loop on every rotation past the unreachable asset.
        logging.debug(
            'Recheck request for %s returned unexpected status %s',
            asset_id,
            response.status_code,
        )


def asset_loop(scheduler: Any) -> None:
    asset = scheduler.get_next_asset()

    if asset is None:
        logging.info(
            'Playlist is empty. Sleeping for %s seconds', EMPTY_PL_DELAY
        )
        view_image(STANDBY_SCREEN)
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=EMPTY_PL_DELAY):
            # Skip was triggered, continue immediately to next iteration
            logging.info(
                'Skip detected during empty playlist wait, continuing'
            )
        else:
            # Duration elapsed normally, continue to next iteration
            pass

    elif _asset_is_displayable(asset):
        name, mime, uri = asset['name'], asset['mimetype'], asset['uri']
        logging.info('Showing asset %s (%s)', name, mime)
        logging.debug('Asset URI %s', uri)
        watchdog()

        if 'image' in mime:
            view_image(uri)
        elif 'web' in mime:
            view_webpage(uri)
        elif 'video' or 'streaming' in mime:
            view_video(uri, asset['duration'])
        else:
            logging.error('Unknown MimeType %s', mime)

        if 'image' in mime or 'web' in mime:
            duration = int(asset['duration'])
            logging.info('Sleeping for %s', duration)
            skip_event = get_skip_event()
            skip_event.clear()
            if skip_event.wait(timeout=duration):
                # Skip was triggered, continue immediately to next iteration
                logging.info('Skip detected, moving to next asset immediately')
            else:
                # Duration elapsed normally, continue to next asset
                pass

    else:
        logging.info(
            'Asset %s at %s is not available, skipping.',
            asset['name'],
            asset['uri'],
        )
        _trigger_asset_recheck(asset.get('asset_id'))
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=0.5):
            # Skip was triggered, continue immediately to next iteration
            logging.info(
                'Skip detected during asset unavailability wait, continuing'
            )
        else:
            # Duration elapsed normally, continue to next iteration
            pass


def setup() -> None:
    global HOME, browser_bus
    HOME = getenv('HOME')
    if not HOME:
        logging.error('No HOME variable')

        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    # Skip event is now handled via threading instead of signals
    signal(SIGALRM, sigalrm)

    load_settings()
    load_browser()

    bus = pydbus.SessionBus()
    browser_bus = bus.get('anthias.webview', '/Anthias')


def start_loop() -> None:
    global loop_is_stopped

    logging.debug('Entering infinite loop.')
    while True:
        if loop_is_stopped:
            sleep(0.1)
            continue

        asset_loop(scheduler)


def main() -> None:
    global scheduler

    setup()

    subscriber = ViewerSubscriber(r, commands)
    subscriber.daemon = True
    subscriber.start()

    # This will prevent white screen from happening before showing the
    # splash screen with IP addresses.
    view_image(STANDBY_SCREEN)

    wait_for_server(SERVER_WAIT_TIMEOUT)

    scheduler = Scheduler()

    if settings['show_splash']:
        view_webpage(SPLASH_PAGE_URL)
        sleep(SPLASH_DELAY)

    # We don't want to show splash page if there are active assets but all of
    # them are not available.
    view_image(STANDBY_SCREEN)

    sleep(0.5)

    start_loop()
