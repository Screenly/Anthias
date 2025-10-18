# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import json
import logging
import sys
from builtins import range
from os import getenv, path
from signal import SIGALRM, signal
from time import sleep

import django
import pydbus
import sh
from future import standard_library
from jinja2 import Template
from tenacity import Retrying, stop_after_attempt, wait_fixed

from settings import LISTEN, ZmqConsumer, settings
from viewer.constants import (
    BALENA_IP_RETRY_DELAY,
    EMPTY_PL_DELAY,
    MAX_BALENA_IP_RETRIES,
    SERVER_WAIT_TIMEOUT,
    SPLASH_DELAY,
    SPLASH_PAGE_URL,
    STANDBY_SCREEN,
)
from viewer.media_player import MediaPlayerProxy
from viewer.playback import navigate_to_asset, play_loop, skip_asset, stop_loop
from viewer.utils import (
    command_not_found,
    get_skip_event,
    sigalrm,
    wait_for_server,
    watchdog,
)

try:
    django.setup()

    # Place imports that uses Django in this block.

    from lib.utils import (
        connect_to_redis,
        get_balena_device_info,
        get_node_ip,
        is_balena_app,
        string_to_bool,
        url_fails,
    )
    from viewer.scheduling import Scheduler
    from viewer.zmq import ZMQ_HOST_PUB_URL, ZmqSubscriber
except Exception:
    pass

standard_library.install_aliases()


__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2024, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"


current_browser_url = None
browser = None
loop_is_stopped = False
browser_bus = None
r = connect_to_redis()

HOME = None

scheduler = None


def send_current_asset_id_to_server():
    consumer = ZmqConsumer()
    consumer.send({'current_asset_id': scheduler.current_asset_id})


def show_hotspot_page(data):
    global loop_is_stopped

    uri = 'http://{0}/hotspot'.format(LISTEN)
    decoded = json.loads(data)

    base_dir = path.abspath(path.dirname(__file__))
    template_path = path.join(base_dir, 'templates/hotspot.html')

    with open(template_path) as f:
        template = Template(f.read())

    context = {
        'network': decoded.get('network', None),
        'ssid_pswd': decoded.get('ssid_pswd', None),
        'address': decoded.get('address', None),
    }

    with open('/data/hotspot/hotspot.html', 'w') as out_file:
        out_file.write(template.render(context=context))

    loop_is_stopped = stop_loop(scheduler)
    view_webpage(uri)


def setup_wifi(data):
    global load_screen_displayed, mq_data
    if not load_screen_displayed:
        mq_data = data
        return

    show_hotspot_page(data)


def show_splash(data):
    global loop_is_stopped

    if is_balena_app():
        while True:
            try:
                ip_address = get_balena_device_info().json()['ip_address']
                if ip_address != '':
                    break
            except Exception:
                break
    else:
        r.set('ip_addresses', data)

    view_webpage(SPLASH_PAGE_URL)
    sleep(SPLASH_DELAY)
    loop_is_stopped = play_loop()


commands = {
    'next': lambda _: skip_asset(scheduler),
    'previous': lambda _: skip_asset(scheduler, back=True),
    'asset': lambda id: navigate_to_asset(scheduler, id),
    'reload': lambda _: load_settings(),
    'stop': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', stop_loop(scheduler)
    ),
    'play': lambda _: setattr(
        __import__('__main__'), 'loop_is_stopped', play_loop()
    ),
    'setup_wifi': lambda data: setup_wifi(data),
    'show_splash': lambda data: show_splash(data),
    'unknown': lambda _: command_not_found(),
    'current_asset_id': lambda _: send_current_asset_id_to_server()
}


def load_browser():
    global browser
    logging.info('Loading browser...')

    browser = sh.Command('ScreenlyWebview')(_bg=True, _err_to_out=True)
    while (
        'Screenly service start' not in browser.process.stdout.decode('utf-8')
    ):
        sleep(1)


def view_webpage(uri):
    global current_browser_url

    if browser is None or not browser.process.alive:
        load_browser()
    if current_browser_url is not uri:
        browser_bus.loadPage(uri)
        current_browser_url = uri
    logging.info('Current url is {0}'.format(current_browser_url))


def view_image(uri):
    global current_browser_url

    if browser is None or not browser.process.alive:
        load_browser()
    if current_browser_url is not uri:
        browser_bus.loadImage(uri)
        current_browser_url = uri
    logging.info('Current url is {0}'.format(current_browser_url))

    if string_to_bool(getenv('WEBVIEW_DEBUG', '0')):
        logging.info(browser.process.stdout)


def view_video(uri, duration):
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


def load_settings():
    """
    Load settings and set the log level.
    """
    settings.load()
    logging.getLogger().setLevel(
        logging.DEBUG if settings['debug_logging'] else logging.INFO
    )


def asset_loop(scheduler):
    asset = scheduler.get_next_asset()

    if asset is None:
        logging.info(
            'Playlist is empty. Sleeping for %s seconds', EMPTY_PL_DELAY)
        view_image(STANDBY_SCREEN)
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=EMPTY_PL_DELAY):
            # Skip was triggered, continue immediately to next iteration
            logging.info('Skip detected during empty playlist wait, continuing')
        else:
            # Duration elapsed normally, continue to next iteration
            pass

    elif (
        path.isfile(asset['uri']) or
        (not url_fails(asset['uri']) or asset['skip_asset_check'])
    ):
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
        logging.info('Asset %s at %s is not available, skipping.',
                     asset['name'], asset['uri'])
        skip_event = get_skip_event()
        skip_event.clear()
        if skip_event.wait(timeout=0.5):
            # Skip was triggered, continue immediately to next iteration
            logging.info('Skip detected during asset unavailability wait, continuing')
        else:
            # Duration elapsed normally, continue to next iteration
            pass


def setup():
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
    browser_bus = bus.get('screenly.webview', '/Screenly')


def wait_for_node_ip(seconds):
    for _ in range(seconds):
        try:
            get_node_ip()
            break
        except Exception:
            sleep(1)


def start_loop():
    global loop_is_stopped

    logging.debug('Entering infinite loop.')
    while True:
        if loop_is_stopped:
            sleep(0.1)
            continue

        asset_loop(scheduler)


def main():
    global scheduler
    global load_screen_displayed, mq_data

    load_screen_displayed = False
    mq_data = None

    setup()

    subscriber_1 = ZmqSubscriber(r, commands, 'tcp://anthias-server:10001')
    subscriber_1.daemon = True
    subscriber_1.start()

    subscriber_2 = ZmqSubscriber(r, commands, ZMQ_HOST_PUB_URL)
    subscriber_2.daemon = True
    subscriber_2.start()

    # This will prevent white screen from happening before showing the
    # splash screen with IP addresses.
    view_image(STANDBY_SCREEN)

    wait_for_server(SERVER_WAIT_TIMEOUT)

    scheduler = Scheduler()

    if settings['show_splash']:
        if is_balena_app():
            for attempt in Retrying(
                stop=stop_after_attempt(MAX_BALENA_IP_RETRIES),
                wait=wait_fixed(BALENA_IP_RETRY_DELAY),
            ):
                with attempt:
                    get_balena_device_info()

        view_webpage(SPLASH_PAGE_URL)
        sleep(SPLASH_DELAY)

    # We don't want to show splash page if there are active assets but all of
    # them are not available.
    view_image(STANDBY_SCREEN)

    load_screen_displayed = True

    if mq_data is not None:
        show_hotspot_page(mq_data)
        mq_data = None

    sleep(0.5)

    start_loop()
