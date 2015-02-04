#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012-2014, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

from datetime import datetime, timedelta
from os import path, getenv, utime
from platform import machine
from random import shuffle
from requests import get as req_get
from requests import head as req_head
from time import sleep, time
from json import load as json_load
from signal import signal, SIGUSR1, SIGUSR2
import logging
import sh

from settings import settings
import html_templates
from utils import url_fails
import db
import assets_helper

SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

BLACK_PAGE = '/tmp/screenly_html/black_page.html'
WATCHDOG_PATH = '/tmp/screenly.watchdog'
SCREENLY_HTML = '/tmp/screenly_html/'
LOAD_SCREEN = '/screenly/loading.jpg'  # relative to $HOME
UZBLRC = '/screenly/misc/uzbl.rc'  # relative to $HOME
INTRO = '/screenly/intro-template.html'

current_browser_url = None
browser = None

VIDEO_TIMEOUT = 20  # secs


def sigusr1(signum, frame):
    """
    The signal interrupts sleep() calls, so the currently playing web or image asset is skipped.
    omxplayer is killed to skip any currently playing video assets.
    """
    logging.info('USR1 received, skipping.')
    sh.killall('omxplayer.bin', _ok_code=[1])


def sigusr2(signum, frame):
    """Reload settings"""
    logging.info("USR2 received, reloading settings.")
    load_settings()


class Scheduler(object):
    def __init__(self, *args, **kwargs):
        logging.debug('Scheduler init')
        self.update_playlist()

    def get_next_asset(self):
        logging.debug('get_next_asset')
        self.refresh_playlist()
        logging.debug('get_next_asset after refresh')
        if self.nassets == 0:
            return None
        idx = self.index
        self.index = (self.index + 1) % self.nassets
        logging.debug('get_next_asset counter %s returning asset %s of %s', self.counter, idx + 1, self.nassets)
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1
        return self.assets[idx]

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
        (self.assets, self.deadline) = generate_asset_list()
        self.nassets = len(self.assets)
        self.counter = 0
        self.index = 0
        logging.debug('update_playlist done, count %s, counter %s, index %s, deadline %s', self.nassets, self.counter, self.index, self.deadline)

    def get_db_mtime(self):
        # get database file last modification time
        try:
            return path.getmtime(settings['database'])
        except:
            return 0


def generate_asset_list():
    logging.info('Generating asset-list...')

    now = datetime.utcnow()
    enabled_assets = [a for a in assets_helper.read(db_conn) if a['is_enabled']]
    future_dates = [a[k] for a in enabled_assets for k in ['start_date', 'end_date'] if a[k] > now]
    deadline = sorted(future_dates)[0] if future_dates else None
    logging.debug('generate_asset_list deadline: %s', deadline)

    playlist = assets_helper.get_playlist(db_conn)
    if settings['shuffle_playlist']:
        shuffle(playlist)
    return (playlist, deadline)


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

    if not url is None:
        current_browser_url = url

    # --config=-       read commands (and config) from stdin
    # --print-events   print events to stdout
    browser = sh.Command('uzbl-browser')(print_events=True, config='-', uri=current_browser_url, _bg=True)
    logging.info('Browser loading %s. Running as PID %s.', current_browser_url, browser.pid)

    uzbl_rc = 'set ssl_verify = {}\n'.format('1' if settings['verify_ssl'] else '0')
    with open(HOME + UZBLRC) as f:  # load uzbl.rc
        uzbl_rc = f.read() + uzbl_rc
    browser_send(uzbl_rc)


def browser_send(command, cb=lambda _: True):
    if not (browser is None) and browser.process.alive:
        while not browser.process._pipe_queue.empty():  # flush stdout
            browser.next()

        browser.process.stdin.put(command + '\n')
        while True:  # loop until cb returns True
            if cb(browser.next()):
                break
    else:
        logging.info('browser found dead, restarting')
        load_browser()


def browser_clear(force=False):
    """Load a black page. Default cb waits for the page to load."""
    browser_url('file://' + BLACK_PAGE, force=force, cb=lambda buf: 'LOAD_FINISH' in buf and BLACK_PAGE in buf)


def browser_url(url, cb=lambda _: True, force=False):
    global current_browser_url

    if url == current_browser_url and not force:
        logging.debug('Already showing %s, keeping it.', current_browser_url)
    else:
        current_browser_url = url
        browser_send('uri ' + current_browser_url, cb=cb)
        logging.info('current url is %s', current_browser_url)


def view_image(uri):
    browser_clear()
    browser_send('js window.setimg("{0}")'.format(uri), cb=lambda b: 'COMMAND_EXECUTED' in b and 'setimg' in b)


def view_video(uri, duration):
    logging.debug('Displaying video %s for %s ', uri, duration)

    if arch == 'armv6l':
        player_args = ['omxplayer', uri]
        player_kwargs = {'o': settings['audio_output'], '_bg': True, '_ok_code': [0, 124]}
        player_kwargs['_ok_code'] = [0, 124]
    else:
        player_args = ['mplayer', uri, '-nosound']
        player_kwargs = {'_bg': True}

    if duration and duration != 'N/A':
        player_args = ['timeout', VIDEO_TIMEOUT + int(duration.split('.')[0])] + player_args

    run = sh.Command(player_args[0])(*player_args[1:], **player_kwargs)

    browser_clear(force=True)
    while run.process.alive:
        watchdog()
        sleep(1)
    if not run.exit_code == 0:
        logging.error('omxplayer exited with exit code %i.' % run.exit_code)


def check_update():
    """
    Check if there is a later version of Screenly-OSE
    available. Only do this update once per day.

    Return True if up to date was written to disk,
    False if no update needed and None if unable to check.
    """

    sha_file = path.join(settings.get_configdir(), 'latest_screenly_sha')

    if path.isfile(sha_file):
        sha_file_mtime = path.getmtime(sha_file)
        last_update = datetime.fromtimestamp(sha_file_mtime)
    else:
        last_update = None

    logging.debug('Last update: %s' % str(last_update))

    if last_update is None or last_update < (datetime.now() - timedelta(days=1)):

        if not url_fails('http://stats.screenlyapp.com'):
            latest_sha = req_get('http://stats.screenlyapp.com/latest')

            if latest_sha.status_code == 200:
                with open(sha_file, 'w') as f:
                    f.write(latest_sha.content.strip())
                return True
            else:
                logging.debug('Received non 200-status')
                return
        else:
            logging.debug('Unable to retreive latest SHA')
            return
    else:
        return False


def load_settings():
    """Load settings and set the log level."""
    settings.load()
    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)


def pro_init():
    """Function to handle first-run on Screenly Pro"""
    is_pro_init = path.isfile(path.join(settings.get_configdir(), 'not_initialized'))

    if is_pro_init:
        logging.debug('Detected Pro initiation cycle.')
        load_browser(url=HOME+INTRO)
    else:
        return False

    status_path = path.join(settings.get_configdir(), 'setup_status.json')
    while is_pro_init:
        with open(status_path, 'rb') as status_file:
            status = json_load(status_file)

        browser_send('js showIpMac("%s", "%s")' % (status.get('ip', ''), status.get('mac', '')))

        if status.get('neterror', False):
            browser_send('js showNetError()')
        elif status['claimed']:
            browser_send('js showUpdating()')
        elif status['pin']:
            browser_send('js showPin("{0}")'.format(status['pin']))

        logging.debug('Waiting for node to be initialized.')
        sleep(5)

    return True


def asset_loop(scheduler):
    check_update()
    asset = scheduler.get_next_asset()

    if asset is None:
        logging.info('Playlist is empty. Sleeping for %s seconds', EMPTY_PL_DELAY)
        view_image(HOME + LOAD_SCREEN)
        sleep(EMPTY_PL_DELAY)

    elif path.isfile(asset['uri']) or not url_fails(asset['uri']):
        name, mime, uri = asset['name'], asset['mimetype'], asset['uri']
        logging.info('Showing asset %s (%s)', name, mime)
        logging.debug('Asset URI %s', uri)
        watchdog()

        if 'image' in mime:
            view_image(uri)
        elif 'web' in mime:
            browser_url(uri)
        elif 'video' in mime:
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
    signal(SIGUSR2, sigusr2)

    load_settings()
    db_conn = db.conn(settings['database'])

    sh.mkdir(SCREENLY_HTML, p=True)
    html_templates.black_page(BLACK_PAGE)


def wait_for_splash_page(url):
    max_retries = 20
    retries = 0
    while retries < max_retries:
        fetch_head = req_head(url)
        if fetch_head.status_code == 200:
            break
        else:
            sleep(1)
            retries += 1
            logging.debug('Waiting for splash-page. Retry %d') % retries


def main():
    setup()
    if pro_init():
        return

    if settings['show_splash']:
        url = 'http://{0}:{1}/splash_page'.format(settings.get_listen_ip(), settings.get_listen_port())
        wait_for_splash_page(url)
        load_browser(url=url)
        sleep(SPLASH_DELAY)
    else:
        url = 'file://' + BLACK_PAGE
        load_browser(url=url)

    scheduler = Scheduler()
    logging.debug('Entering infinite loop.')
    while True:
        asset_loop(scheduler)


if __name__ == "__main__":
    main()
