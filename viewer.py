#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import pydbus
import random
import re
import sh
import string
import zmq
from datetime import datetime, timedelta
from mixpanel import Mixpanel, MixpanelException
from netifaces import gateways
from os import path, getenv, utime, system
from platform import machine
from random import shuffle
from threading import Thread
from requests import get as req_get
from signal import alarm, signal, SIGALRM, SIGUSR1
from time import sleep

from settings import settings, LISTEN, PORT, ZmqConsumer

from lib import assets_helper, html_templates
from lib import db
from lib.diagnostics import get_git_branch, get_git_short_hash
from lib.github import fetch_remote_hash, remote_branch_available
from lib.errors import SigalrmException
from lib.utils import get_active_connections, url_fails, touch, is_balena_app, is_ci, get_node_ip


__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2019, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"


SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

INITIALIZED_FILE = '/.screenly/initialized'
BLACK_PAGE = '/tmp/screenly_html/black_page.html'
WATCHDOG_PATH = '/tmp/screenly.watchdog'
SCREENLY_HTML = '/tmp/screenly_html/'
LOAD_SCREEN = '/screenly/static/img/loading.png'  # relative to $HOME
UZBLRC = '/.config/uzbl/config-screenly'  # relative to $HOME
INTRO = '/screenly/intro-template.html'

current_browser_url = None
browser = None
loop_is_stopped = False

VIDEO_TIMEOUT = 20  # secs

HOME = None
arch = None
db_conn = None

scheduler = None


def sigalrm(signum, frame):
    """
    Signal just throw an SigalrmException
    """
    raise SigalrmException("SigalrmException")


def sigusr1(signum, frame):
    """
    The signal interrupts sleep() calls, so the currently
    playing web or image asset is skipped.
    omxplayer is killed to skip any currently playing video assets.
    """
    logging.info('USR1 received, skipping.')
    try:
        sh.killall('omxplayer.bin', _ok_code=[1])
    except OSError:
        pass


def skip_asset(back=False):
    if back is True:
        scheduler.reverse = True
    system('pkill -SIGUSR1 -f viewer.py')


def navigate_to_asset(asset_id):
    scheduler.extra_asset = asset_id
    system('pkill -SIGUSR1 -f viewer.py')


def stop_loop():
    global db_conn, loop_is_stopped
    loop_is_stopped = True
    skip_asset()
    db_conn = None


def play_loop():
    global loop_is_stopped
    loop_is_stopped = False


def command_not_found():
    logging.error("Command not found")


def send_current_asset_id_to_server():
    consumer = ZmqConsumer()
    consumer.send({'current_asset_id': scheduler.current_asset_id})


commands = {
    'next': lambda _: skip_asset(),
    'previous': lambda _: skip_asset(back=True),
    'asset': lambda id: navigate_to_asset(id),
    'reload': lambda _: load_settings(),
    'stop': lambda _: stop_loop(),
    'play': lambda _: play_loop(),
    'unknown': lambda _: command_not_found(),
    'current_asset_id': lambda _: send_current_asset_id_to_server()
}


class ZmqSubscriber(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.context = zmq.Context()

    def run(self):
        socket = self.context.socket(zmq.SUB)
        socket.connect('tcp://{}:10001'.format(LISTEN))
        socket.setsockopt(zmq.SUBSCRIBE, 'viewer')
        while True:
            msg = socket.recv()
            topic, message = msg.split()

            # If the command consists of 2 parts, then the first is the function, the second is the argument
            parts = message.split('&')
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
            if asset and asset['is_processing'] == 0:
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
        logging.debug('get_next_asset counter %s returning asset %s of %s', self.counter, idx + 1, len(self.assets))
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1

        current_asset = self.assets[idx]
        self.current_asset_id = current_asset.get('asset_id')
        return current_asset

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = datetime.utcnow()
        logging.debug('refresh: counter: (%s) deadline (%s) timecur (%s)', self.counter, self.deadline, time_cur)
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
        # Try to keep the same position in the play list. E.g. if a new asset is added to the end of the list, we
        # don't want to start over from the beginning.
        self.index = self.index % len(self.assets) if self.assets else 0
        logging.debug('update_playlist done, count %s, counter %s, index %s, deadline %s', len(self.assets),
                      self.counter, self.index, self.deadline)

    def get_db_mtime(self):
        # get database file last modification time
        try:
            return path.getmtime(settings['database'])
        except (OSError, TypeError):
            return 0


def get_specific_asset(asset_id):
    logging.info('Getting specific asset')
    return assets_helper.read(db_conn, asset_id)


def generate_asset_list():
    """Choose deadline via:
        1. Map assets to deadlines with rule: if asset is active then 'end_date' else 'start_date'
        2. Get nearest deadline
    """
    logging.info('Generating asset-list...')
    assets = assets_helper.read(db_conn)
    deadlines = [asset['end_date'] if assets_helper.is_active(asset) else asset['start_date'] for asset in assets]

    playlist = filter(assets_helper.is_active, assets)
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


def load_browser(url=None):
    global browser, current_browser_url
    logging.info('Loading browser...')

    if browser:
        logging.info('killing previous uzbl %s', browser.pid)
        browser.process.kill()

    if url is not None:
        current_browser_url = url

    # --config=-       read commands (and config) from stdin
    # --print-events   print events to stdout
    browser = sh.Command('uzbl-browser')(print_events=True, config='-', uri=current_browser_url, _bg=True)
    logging.info('Browser loading %s. Running as PID %s.', current_browser_url, browser.pid)

    uzbl_rc = 'set ssl_verify = {}\n'.format('1' if settings['verify_ssl'] else '0')
    with open(HOME + UZBLRC) as f:  # load uzbl.rc
        uzbl_rc = f.read() + uzbl_rc
    browser_send(uzbl_rc)


def browser_get_event():
    alarm(10)
    try:
        event = browser.next()
    except SigalrmException:
        return None
    alarm(0)
    return event


def browser_send(command, cb=lambda _: True):
    if not (browser is None) and browser.process.alive:
        while not browser.process._pipe_queue.empty():  # flush stdout
            browser_get_event()

        browser.process.stdin.put(command + '\n')
        while True:  # loop until cb returns True
            try:
                browser_event = browser_get_event()
            except StopIteration:
                break
            if not browser_event:
                break
            if cb(browser_event):
                break
    else:
        logging.info('browser found dead, restarting')
        load_browser()


def browser_clear(force=False):
    """Load a black page. Default cb waits for the page to load."""
    browser_url('file://' + BLACK_PAGE, force=force,
                cb=lambda buf: 'LOAD_FINISH' in buf and BLACK_PAGE in buf)


def browser_url(url, cb=lambda _: True, force=False):
    global current_browser_url

    if url == current_browser_url and not force:
        logging.debug('Already showing %s, reloading it.', current_browser_url)
    else:
        current_browser_url = url

        """Uzbl handles full URI format incorrect: scheme://uname:passwd@domain:port/path
        We need to escape @"""
        escaped_url = current_browser_url.replace('@', '\\@').replace('&amp;amp;', '\\&')

        browser_send('uri ' + escaped_url, cb=cb)
        logging.info('current url is %s', current_browser_url)


def view_image(uri):
    browser_clear()
    browser_send('js window.setimg("{0}")'.format(uri),
                 cb=lambda b: 'COMMAND_EXECUTED' in b and 'setimg' in b)


def view_video(uri, duration):
    logging.debug('Displaying video %s for %s ', uri, duration)

    if arch in ('armv6l', 'armv7l'):
        player_args = ['omxplayer', uri]
        player_kwargs = {'o': settings['audio_output'], '_bg': True, '_ok_code': [0, 124, 143]}
    else:
        player_args = ['mplayer', uri, '-nosound']
        player_kwargs = {'_bg': True, '_ok_code': [0, 124]}

    if duration and duration != 'N/A':
        player_args = ['timeout', VIDEO_TIMEOUT + int(duration.split('.')[0])] + player_args

    run = sh.Command(player_args[0])(*player_args[1:], **player_kwargs)

    browser_clear(force=True)
    try:
        while run.process.alive:
            watchdog()
            sleep(1)
        if run.exit_code == 124:
            logging.error('omxplayer timed out')
    except sh.ErrorReturnCode_1:
        logging.info('Resource URI is not correct, remote host is not responding or request was rejected.')


def check_update():
    """
    Check if there is a later version of Screenly OSE
    available. Only do this update once per day.
    Return True if up to date was written to disk,
    False if no update needed and None if unable to check.
    """

    sha_file = path.join(settings.get_configdir(), 'latest_screenly_sha')
    device_id_file = path.join(settings.get_configdir(), 'device_id')

    if path.isfile(sha_file):
        sha_file_mtime = path.getmtime(sha_file)
        last_update = datetime.fromtimestamp(sha_file_mtime)
    else:
        last_update = None

    if not path.isfile(device_id_file):
        device_id = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(15))
        with open(device_id_file, 'w') as f:
            f.write(device_id)
    else:
        with open(device_id_file, 'r') as f:
            device_id = f.read()

    logging.debug('Last update: %s' % str(last_update))

    git_branch = get_git_branch()
    git_hash = get_git_short_hash()

    if last_update is None or last_update < (datetime.now() - timedelta(days=1)):

        if not settings['analytics_opt_out'] and not is_ci():
            mp = Mixpanel('d18d9143e39ffdb2a4ee9dcc5ed16c56')
            try:
                mp.track(device_id, 'Version', {
                    'Branch': str(git_branch),
                    'Hash': str(git_hash),
                    'NOOBS': path.isfile('/boot/os_config.json'),
                    'Balena': is_balena_app()
                })
            except MixpanelException:
                pass
            except AttributeError:
                pass

        if remote_branch_available(git_branch):
            latest_sha = fetch_remote_hash(git_branch)

            if latest_sha:
                with open(sha_file, 'w') as f:
                    f.write(latest_sha)
                return True
            else:
                logging.debug('Unable to fetch latest hash.')
                return
        else:
            touch(sha_file)
            logging.debug('Unable to check if branch exist. Checking again tomorrow.')
            return
    else:
        return False


def load_settings():
    """Load settings and set the log level."""
    settings.load()
    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)


def asset_loop(scheduler):
    disable_update_check = getenv("DISABLE_UPDATE_CHECK", False)
    if not disable_update_check:
        check_update()
    asset = scheduler.get_next_asset()

    if asset is None:
        logging.info('Playlist is empty. Sleeping for %s seconds', EMPTY_PL_DELAY)
        view_image(HOME + LOAD_SCREEN)
        sleep(EMPTY_PL_DELAY)

    elif path.isfile(asset['uri']) or (not url_fails(asset['uri']) or asset['skip_asset_check']):
        name, mime, uri = asset['name'], asset['mimetype'], asset['uri']
        logging.info('Showing asset %s (%s)', name, mime)
        logging.debug('Asset URI %s', uri)
        watchdog()

        if 'image' in mime:
            view_image(uri)
        elif 'web' in mime:
            # FIXME If we want to force periodic reloads of repeated web assets, force=True could be used here.
            # See e38e6fef3a70906e7f8739294ffd523af6ce66be.
            browser_url(uri)
        elif 'video' or 'streaming' in mime:
            view_video(uri, asset['duration'])
        else:
            logging.error('Unknown MimeType %s', mime)

        if 'image' in mime or 'web' in mime:
            duration = int(asset['duration'])
            logging.info('Sleeping for %s', duration)
            sleep(duration)

    else:
        logging.info('Asset %s at %s is not available, skipping.', asset['name'], asset['uri'])
        sleep(0.5)


def setup():
    global HOME, arch, db_conn
    HOME = getenv('HOME', '/home/pi')
    arch = machine()

    signal(SIGUSR1, sigusr1)
    signal(SIGALRM, sigalrm)

    load_settings()
    db_conn = db.conn(settings['database'])

    sh.mkdir(SCREENLY_HTML, p=True)
    html_templates.black_page(BLACK_PAGE)


def setup_hotspot():
    bus = pydbus.SystemBus()

    pattern_include = re.compile("wlan*")
    pattern_exclude = re.compile("ScreenlyOSE-*")

    wireless_connections = get_active_connections(bus)

    if wireless_connections is None:
        return

    wireless_connections = filter(
        lambda c: not pattern_exclude.search(str(c['Id'])),
        filter(
            lambda c: pattern_include.search(str(c['Devices'])),
            wireless_connections
        )
    )

    # Displays the hotspot page

    if not path.isfile(HOME + INITIALIZED_FILE) and not gateways().get('default'):
        if len(wireless_connections) == 0:
            url = 'http://{0}/hotspot'.format(LISTEN)
            load_browser(url=url)

    # Wait until the network is configured
    while not path.isfile(HOME + INITIALIZED_FILE) and not gateways().get('default'):
        if len(wireless_connections) == 0:
            sleep(1)
            wireless_connections = filter(
                lambda c: not pattern_exclude.search(str(c['Id'])),
                filter(
                    lambda c: pattern_include.search(str(c['Devices'])),
                    get_active_connections(bus)
                )
            )
            continue
        if wireless_connections is None:
            sleep(1)
            continue
        break

    wait_for_node_ip(5)


def wait_for_node_ip(seconds):
    for _ in range(seconds):
        try:
            get_node_ip()
            break
        except Exception:
            sleep(1)


def main():
    global db_conn, scheduler
    setup()

    subscriber = ZmqSubscriber()
    subscriber.daemon = True
    subscriber.start()

    scheduler = Scheduler()

    if is_balena_app():
        load_browser()
    else:
        setup_hotspot()

    url = 'http://{0}:{1}/splash-page'.format(LISTEN, PORT) if settings['show_splash'] else 'file://' + BLACK_PAGE
    browser_url(url=url)

    if settings['show_splash']:
        sleep(SPLASH_DELAY)

    # We don't want to show splash-page if there are active assets but all of them are not available
    view_image(HOME + LOAD_SCREEN)

    logging.debug('Entering infinite loop.')
    while True:
        if loop_is_stopped:
            sleep(0.1)
            continue
        if not db_conn:
            load_settings()
            db_conn = db.conn(settings['database'])

        asset_loop(scheduler)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Viewer crashed.")
        raise
