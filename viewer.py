# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import json
import logging
import sys
from builtins import bytes, object, range
from os import getenv, path, system, utime
from random import shuffle
from signal import SIGALRM, SIGUSR1, signal
from threading import Thread
from time import sleep

import django
import pydbus
import requests
import sh
import zmq
from future import standard_library
from jinja2 import Template
from tenacity import Retrying, stop_after_attempt, wait_fixed

from lib.errors import SigalrmError
from lib.media_player import MediaPlayerProxy
from settings import LISTEN, PORT, ZmqConsumer, settings

try:
    django.setup()

    # Place imports that uses Django in this block.

    from django.utils import timezone

    from anthias_app.models import Asset
    from lib.utils import (
        connect_to_redis,
        get_balena_device_info,
        get_node_ip,
        is_balena_app,
        string_to_bool,
        url_fails,
    )
except Exception:
    pass


standard_library.install_aliases()


__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2024, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"


SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

INITIALIZED_FILE = '/.screenly/initialized'
WATCHDOG_PATH = '/tmp/screenly.watchdog'

STANDBY_SCREEN = f'http://{LISTEN}:{PORT}/static/img/standby.png'
SPLASH_PAGE_URL = f'http://{LISTEN}:{PORT}/splash-page'
ZMQ_HOST_PUB_URL = 'tcp://host.docker.internal:10001'

MAX_BALENA_IP_RETRIES = 90
BALENA_IP_RETRY_DELAY = 1
SERVER_WAIT_TIMEOUT = 60

current_browser_url = None
browser = None
loop_is_stopped = False
browser_bus = None
r = connect_to_redis()

HOME = None

scheduler = None


def sigalrm(signum, frame):
    """
    Signal just throw an SigalrmError
    """
    raise SigalrmError("SigalrmError")


def sigusr1(signum, frame):
    """
    The signal interrupts sleep() calls, so the currently
    playing web or image asset is skipped.
    """
    logging.info('USR1 received, skipping.')
    MediaPlayerProxy.get_instance().stop()


def skip_asset(back=False):
    if back is True:
        scheduler.reverse = True
    system('pkill -SIGUSR1 -f viewer.py')


def navigate_to_asset(asset_id):
    scheduler.extra_asset = asset_id
    system('pkill -SIGUSR1 -f viewer.py')


def stop_loop():
    global loop_is_stopped
    loop_is_stopped = True
    skip_asset()


def play_loop():
    global loop_is_stopped
    loop_is_stopped = False


def command_not_found():
    logging.error("Command not found")


def send_current_asset_id_to_server():
    consumer = ZmqConsumer()
    consumer.send({'current_asset_id': scheduler.current_asset_id})


def show_hotspot_page(data):
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

    stop_loop()
    view_webpage(uri)


def setup_wifi(data):
    global load_screen_displayed, mq_data
    if not load_screen_displayed:
        mq_data = data
        return

    show_hotspot_page(data)


def show_splash(data):
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
    play_loop()


commands = {
    'next': lambda _: skip_asset(),
    'previous': lambda _: skip_asset(back=True),
    'asset': lambda id: navigate_to_asset(id),
    'reload': lambda _: load_settings(),
    'stop': lambda _: stop_loop(),
    'play': lambda _: play_loop(),
    'setup_wifi': lambda data: setup_wifi(data),
    'show_splash': lambda data: show_splash(data),
    'unknown': lambda _: command_not_found(),
    'current_asset_id': lambda _: send_current_asset_id_to_server()
}


class ZmqSubscriber(Thread):
    def __init__(self, publisher_url, topic='viewer'):
        Thread.__init__(self)
        self.context = zmq.Context()
        self.publisher_url = publisher_url
        self.topic = topic

    def run(self):
        socket = self.context.socket(zmq.SUB)
        socket.connect(self.publisher_url)
        socket.setsockopt(zmq.SUBSCRIBE, bytes(self.topic, encoding='utf-8'))

        if self.publisher_url == ZMQ_HOST_PUB_URL:
            r.set('viewer-subscriber-ready', int(True))

        while True:
            msg = socket.recv()
            topic, message = msg.decode('utf-8').split(' ', 1)

            # If the command consists of 2 parts, then the first is the
            # function and the second is the argument.
            parts = message.split('&', 1)
            command = parts[0]
            parameter = parts[1] if len(parts) > 1 else None

            commands.get(command, commands.get('unknown'))(parameter)


class Scheduler(object):
    def __init__(self, *args, **kwargs):
        logging.debug('Scheduler init')
        self.assets = []
        self.counter = 0
        self.current_asset_id = None
        self.deadline = None
        self.extra_asset = None
        self.index = 0
        self.reverse = 0
        self.update_playlist()

    def get_next_asset(self):
        logging.debug('get_next_asset')

        if self.extra_asset is not None:
            asset = get_specific_asset(self.extra_asset)
            if asset and asset['is_processing']:
                self.current_asset_id = self.extra_asset
                self.extra_asset = None
                return asset
            logging.error("Asset not found or processed")
            self.extra_asset = None

        self.refresh_playlist()
        logging.debug('get_next_asset after refresh')
        if not self.assets:
            self.current_asset_id = None
            return None
        if self.reverse:
            idx = (self.index - 2) % len(self.assets)
            self.index = (self.index - 1) % len(self.assets)
            self.reverse = False
        else:
            idx = self.index
            self.index = (self.index + 1) % len(self.assets)

        logging.debug(
            'get_next_asset counter %s returning asset %s of %s',
            self.counter, idx + 1, len(self.assets),
        )

        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1

        current_asset = self.assets[idx]
        self.current_asset_id = current_asset.get('asset_id')
        return current_asset

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = timezone.now()

        logging.debug(
            'refresh: counter: (%s) deadline (%s) timecur (%s)',
            self.counter, self.deadline, time_cur
        )

        if self.get_db_mtime() > self.last_update_db_mtime:
            logging.debug('updating playlist due to database modification')
            self.update_playlist()
        elif settings['shuffle_playlist'] and self.counter >= 5:
            self.update_playlist()
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def update_playlist(self):
        logging.debug('update_playlist')
        self.last_update_db_mtime = self.get_db_mtime()
        (new_assets, new_deadline) = generate_asset_list()
        if new_assets == self.assets and new_deadline == self.deadline:
            # If nothing changed, don't disturb the current play-through.
            return

        self.assets, self.deadline = new_assets, new_deadline
        self.counter = 0
        # Try to keep the same position in the play list. E.g., if a new asset
        # is added to the end of the list, we don't want to start over from
        # the beginning.
        self.index = self.index % len(self.assets) if self.assets else 0
        logging.debug(
            'update_playlist done, count %s, counter %s, index %s, deadline %s',  # noqa: E501
            len(self.assets), self.counter, self.index, self.deadline
        )

    def get_db_mtime(self):
        # get database file last modification time
        try:
            return path.getmtime(settings['database'])
        except (OSError, TypeError):
            return 0


def get_specific_asset(asset_id):
    logging.info('Getting specific asset')
    try:
        return Asset.objects.get(asset_id=asset_id).__dict__
    except Asset.DoesNotExist:
        logging.debug('Asset %s not found in database', asset_id)
        return None


def generate_asset_list():
    """Choose deadline via:
        1. Map assets to deadlines with rule: if asset is active then
           'end_date' else 'start_date'
        2. Get nearest deadline
    """
    logging.info('Generating asset-list...')
    assets = Asset.objects.all()
    deadlines = [
        asset.end_date
        if asset.is_active()
        else asset.start_date
        for asset in assets
    ]

    enabled_assets = Asset.objects.filter(
        is_enabled=True,
        start_date__isnull=False,
        end_date__isnull=False,
    ).order_by('play_order')
    playlist = [
        {
            k: v for k, v in asset.__dict__.items()
            if k not in ['_state', 'md5']
        }
        for asset in enabled_assets
        if asset.is_active()
    ]

    deadline = sorted(deadlines)[0] if len(deadlines) > 0 else None
    logging.debug('generate_asset_list deadline: %s', deadline)

    if settings['shuffle_playlist']:
        shuffle(playlist)

    return playlist, deadline


def watchdog():
    """Notify the watchdog file to be used with the watchdog-device."""
    if not path.isfile(WATCHDOG_PATH):
        open(WATCHDOG_PATH, 'w').close()
    else:
        utime(WATCHDOG_PATH, None)


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
        while media_player.is_playing():
            watchdog()
            sleep(1)
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
        sleep(EMPTY_PL_DELAY)

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
            sleep(duration)

    else:
        logging.info('Asset %s at %s is not available, skipping.',
                     asset['name'], asset['uri'])
        sleep(0.5)


def setup():
    global HOME, browser_bus
    HOME = getenv('HOME')
    if not HOME:
        logging.error('No HOME variable')

        # Alternatively, we can raise an Exception using a custom message,
        # or we can create a new class that extends Exception.
        sys.exit(1)

    signal(SIGUSR1, sigusr1)
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


def wait_for_server(retries, wt=1):
    for _ in range(retries):
        try:
            response = requests.get(f'http://{LISTEN}:{PORT}/splash-page')
            response.raise_for_status()
            break
        except requests.exceptions.RequestException:
            sleep(wt)


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

    subscriber_1 = ZmqSubscriber('tcp://anthias-server:10001')
    subscriber_1.daemon = True
    subscriber_1.start()

    subscriber_2 = ZmqSubscriber(ZMQ_HOST_PUB_URL)
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


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Viewer crashed.")
        raise
