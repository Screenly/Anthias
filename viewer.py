#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012-2013, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

from datetime import datetime, timedelta
from glob import glob
from os import path, getenv, remove, makedirs
from os import stat as os_stat, utime, system, kill
from platform import machine
from random import shuffle
from requests import get as req_get, head as req_head
from stat import S_ISFIFO
from subprocess import Popen
from time import sleep, time
from sh import feh
import logging
import signal

from settings import settings
import html_templates

from utils import url_fails

import db
import assets_helper
# Define to none to ensure we refresh
# the settings.
last_settings_refresh = None
load_screen_pid = None
is_pro_init = None

# Detect the architecture and load the proper video player
arch = machine()
if arch == 'armv6l':
    from sh import omxplayer
elif arch in ['x86_64', 'x86_32']:
    from sh import mplayer


def get_is_pro_init():
    """
    Function to handle first-run on Screenly Pro
    """
    if path.isfile('/home/pi/.screenly/not_initialized'):
        return False
    else:
        return True


def sigusr1(signum, frame):
    """
    This is the signal handler for SIGUSR1
    The signal interrupts sleep() calls, so
    the currently running asset will be skipped.
    Since video assets don't have a duration field,
    the video player has to be killed.
    """
    logging.info("Signal received, skipping.")
    system("killall omxplayer.bin")


def sigusr2(signum, frame):
    """
    This is the signal handler for SIGUSR2
    Resets the last_settings_refresh timestamp to force
    settings reloading.
    """
    global last_settings_refresh
    logging.info("Signal received, reloading settings.")
    last_settings_refresh = None
    reload_settings()


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
        logging.debug('get_next_asset counter %d returning asset %d of %d' % (self.counter, idx + 1, self.nassets))
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1
        return self.assets[idx]

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = datetime.utcnow()
        logging.debug('refresh: counter: (%d) deadline (%s) timecur (%s)' % (self.counter, self.deadline, time_cur))
        if self.dbisnewer():
            self.update_playlist()
        elif settings['shuffle_playlist'] and self.counter >= 5:
            self.update_playlist()
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def update_playlist(self):
        logging.debug('update_playlist')
        (self.assets, self.deadline) = generate_asset_list()
        self.nassets = len(self.assets)
        self.gentime = time()
        self.counter = 0
        self.index = 0
        logging.debug('update_playlist done, count %d, counter %d, index %d, deadline %s' % (self.nassets, self.counter, self.index, self.deadline))

    def dbisnewer(self):
        # get database file last modification time
        try:
            db_mtime = path.getmtime(settings.get_database())
        except:
            db_mtime = 0
        return db_mtime >= self.gentime


def generate_asset_list():
    logging.info('Generating asset-list...')
    playlist = assets_helper.get_playlist(db_conn)
    deadline = sorted([asset['end_date'] for asset in playlist])[0] if len(playlist) > 0 else None
    logging.debug('generate_asset_list deadline: %s' % deadline)

    if settings['shuffle_playlist']:
        shuffle(playlist)

    return (playlist, deadline)


def watchdog():
    """
    Notify the watchdog file to be used with the watchdog-device.
    """

    watchdog = '/tmp/screenly.watchdog'
    if not path.isfile(watchdog):
        open(watchdog, 'w').close()
    else:
        utime(watchdog, None)


def asset_is_accessible(uri):
    """
    Determine if content is accessible or not.
    """

    asset_folder = path.join(getenv('HOME'), 'screenly_assets')
    # If it's local content, just check if the file exist on disk.

    if ((asset_folder in uri) or (html_folder in uri) and path.exists(uri)):
        return True

    try:
        # Give up if we can't even get the header in five seconds.
        remote_asset_status = req_head(uri, timeout=5, allow_redirects=True).status_code
        if remote_asset_status == 200:
            return True
        else:
            return False
    except:
        return False


def load_browser():
    logging.info('Loading browser...')
    browser_bin = "uzbl-browser"
    browser_resolution = settings['resolution']

    global is_pro_init
    is_pro_init = get_is_pro_init()
    if not is_pro_init:
        logging.debug('Detected Pro initiation cycle.')

        # Wait for the intro file to exist (if it doesn't)
        intro_file = '/home/pi/.screenly/intro.html'
        while not path.isfile(intro_file):
            logging.debug('intro.html missing. Going to sleep.')
            sleep(0.5)

        browser_load_url = 'file://' + intro_file

    elif settings['show_splash']:
        browser_load_url = "http://%s:%s/splash_page" % (settings.get_listen_ip(), settings.get_listen_port())
    else:
        browser_load_url = black_page

    browser_args = [browser_bin, "--geometry=" + browser_resolution, "--uri=" + browser_load_url]
    browser = Popen(browser_args)

    logging.info('Browser loaded. Running as PID %d.' % browser.pid)

    if settings['show_splash']:
        # Show splash screen for 60 seconds.
        sleep(60)
    else:
        # Give browser some time to start (we have seen multiple uzbl running without this)
        sleep(10)

    return browser


def get_fifo():
    """
    Look for UZBL's FIFO-file in /tmp.
    Don't give up until it has been found.
    """
    found_fifo = False
    fifo = None

    logging.debug('Looking for UZBL fifo...')

    while not found_fifo:
        candidates = glob('/tmp/uzbl_fifo_*')
        for file in candidates:
            if S_ISFIFO(os_stat(file).st_mode):
                found_fifo = True
                fifo = file
        sleep(0.5)
    logging.debug('Found UZBL fifo  in %s.' % file)
    return fifo


def browser_fifo(data):
    f = open(fifo, 'a')
    f.write('%s\n' % data)
    f.close()


def browser_reload(force=False):
    """
    Reload the browser. Use to Force=True to force-reload
    """

    if not force:
        reload_command = 'reload'
    else:
        reload_command = 'reload_ign_cache'

    browser_fifo(reload_command)


def browser_url(url):
    browser_fifo('set uri = %s' % url)


def disable_browser_status():
    logging.debug('Disabled status-bar in browser')
    browser_fifo('set show_status = 0')


def view_image(uri, duration):
    logging.debug('Displaying image %s for %s seconds.' % (uri, duration))

    if asset_is_accessible(uri):
        feh('--scale-down', '--borderless', '--fullscreen', '--cycle-once', '--slideshow-delay', duration,  uri)
    else:
        logging.debug('Received non-200 status (or file not found if local) from %s. Skipping.' % (uri))


def view_video(uri):

    ## For Raspberry Pi
    if arch == 'armv6l':
        logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % uri)

        if asset_is_accessible(uri):
            run = omxplayer('-o', settings['audio_output'], str(uri))
        else:
            logging.debug('Content is unaccessible. Skipping...')
            return

        if run.exit_code != 0:
            logging.debug("Unclean exit: " + str(run))

        # Clean up after omxplayer
        omxplayer_logfile = path.join(getenv('HOME'), 'omxplayer.log')
        if path.isfile(omxplayer_logfile):
            remove(omxplayer_logfile)

    ## For x86
    elif arch in ['x86_64', 'x86_32']:
        logging.debug('Displaying video %s. Detected x86. Using mplayer.' % uri)

        if asset_is_accessible(uri):
            run = mplayer('-fs', '-nosound', str(uri))
        else:
            logging.debug('Content is unaccessible. Skipping...')
            return

        if run.exit_code != 0:
            logging.debug("Unclean exit: " + str(run))


def view_web(url, duration):
    if asset_is_accessible(url):
        logging.debug('Web content appears to be available. Proceeding.')
        logging.debug('Displaying url %s for %s seconds.' % (url, duration))

        browser_url(url)

        sleep(int(duration))

        browser_url(black_page)
    else:
        logging.debug('Received non-200 status (or file not found if local) from %s. Skipping.' % (url))


def toggle_load_screen(status=True):
    """
    Toggle the load screen. Set status to either True or False.
    """
    load_screen = '/home/pi/screenly/loading.jpg'
    global load_screen_pid

    if (status and path.isfile(load_screen)):
        image_loader = feh('--scale-down', '--borderless', '--fullscreen', load_screen, _bg=True)
        load_screen_pid = image_loader.pid
        return image_loader.pid

    elif not status and load_screen_pid:
        kill(load_screen_pid, signal.SIGTERM)
        load_screen_pid = None
        return True
    else:
        return False


def check_update():
    """
    Check if there is a later version of Screenly-OSE
    available. Only do this update once per day.

    Return True if up to date was written to disk,
    False if no update needed and None if unable to check.
    """

    sha_file = path.join(getenv('HOME'), '.screenly', 'latest_screenly_sha')

    if path.isfile(sha_file):
        sha_file_mtime = path.getmtime(sha_file)
        last_update = datetime.fromtimestamp(sha_file_mtime)
    else:
        last_update = None

    logging.debug('Last update: %s' % str(last_update))

    if last_update is None or last_update < (datetime.now() - timedelta(days=1)):

        if asset_is_accessible('http://stats.screenlyapp.com'):
            latest_sha = req_get('http://stats.screenlyapp.com/latest')

            if latest_sha.status_code == 200:
                with open(sha_file, 'w') as f:
                    f.write(latest_sha.content.strip())
                return True
            else:
                logging.debug('Received on 200-status')
                return
        else:
            logging.debug('Unable to retreive latest SHA')
            return
    else:
        return False


def reload_settings():
    """
    Reload settings if the timestamp of the
    settings file is newer than the settings
    file loaded in memory.
    """

    settings_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')
    settings_file_mtime = path.getmtime(settings_file)
    settings_file_timestamp = datetime.fromtimestamp(settings_file_mtime)

    if not last_settings_refresh or settings_file_timestamp > last_settings_refresh:
        settings.load()

    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)

    global last_setting_refresh
    last_setting_refresh = datetime.utcnow()


if __name__ == "__main__":

    # Bring up load screen
    toggle_load_screen(True)

    # Install signal handlers
    signal.signal(signal.SIGUSR1, sigusr1)
    signal.signal(signal.SIGUSR2, sigusr2)

    # Before we start, reload the settings.
    reload_settings()

    global db_conn
    db_conn = db.conn(settings.get_database())

    # Create folder to hold HTML-pages
    html_folder = '/tmp/screenly_html/'
    if not path.isdir(html_folder):
        makedirs(html_folder)

    # Set up HTML templates
    black_page = html_templates.black_page()

    # Fire up the browser
    run_browser = load_browser()

    logging.debug('Getting browser PID.')
    browser_pid = run_browser.pid

    logging.debug('Getting FIFO.')
    fifo = get_fifo()

    logging.debug('Disable the browser status bar.')
    disable_browser_status()

    # Disable load screen early if initialization mode
    if not is_pro_init:
        toggle_load_screen(False)

    # Wait until initialized (Pro only).
    while not get_is_pro_init():
        logging.debug('Waiting for node to be initialized.')
        browser_reload(force=True)
        sleep(5)

    # Bring up the blank page (in case there are only videos).
    logging.debug('Loading blank page.')
    view_web(black_page, 1)

    scheduler = Scheduler()

    # Disable load screen
    toggle_load_screen(False)

    # Infinite loop.
    logging.debug('Entering infinite loop.')
    while True:
        asset = scheduler.get_next_asset()
        logging.debug('got asset' + str(asset))

        is_up_to_date = check_update()
        logging.debug('Check update: %s' % str(is_up_to_date))

        if asset is None:
            # The playlist is empty, go to sleep.
            logging.info('Playlist is empty. Going to sleep.')
            sleep(5)
        elif not url_fails(asset['uri']):
            logging.info('Showing asset %s.' % asset["name"])

            watchdog()

            if "image" in asset["mimetype"]:
                view_image(asset['uri'], asset["duration"])
            elif "video" in asset["mimetype"]:
                view_video(asset["uri"])
            elif "web" in asset["mimetype"]:
                view_web(asset["uri"], asset["duration"])
            else:
                print "Unknown MimeType, or MimeType missing"
        else:
            logging.info('Asset {0} is not available, skipping.'.format(asset['uri']))
