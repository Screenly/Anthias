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
from time import sleep, time
import json
import logging
import sh
import signal

from settings import settings
import html_templates

from utils import url_fails

import db
import assets_helper
# Define to none to ensure we refresh
# the settings.
last_settings_refresh = None
is_pro_init = None
current_browser_url = None

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
    if path.isfile(path.join(settings.get_configdir(), 'not_initialized')):
        return False
    else:
        return True


def sigusr1(signum, frame):
    """
    The signal interrupts sleep() calls, so the currently playing web or image asset is skipped.
    omxplayer is killed to skip any currently playing video assets.
    """
    logging.info('USR1 received, skipping.')
    sh.killall('omxplayer.bin')


def sigusr2(signum, frame):
    """Reload settings"""
    global last_settings_refresh
    logging.info("USR2 received, reloading settings.")
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
        logging.debug('get_next_asset counter %s returning asset %s of %s', self.counter, idx + 1, self.nassets)
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1
        return self.assets[idx]

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = datetime.utcnow()
        logging.debug('refresh: counter: (%s) deadline (%s) timecur (%s)', self.counter, self.deadline, time_cur)
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
        logging.debug('update_playlist done, count %s, counter %s, index %s, deadline %s', self.nassets, self.counter, self.index, self.deadline)

    def dbisnewer(self):
        # get database file last modification time
        try:
            db_mtime = path.getmtime(settings['database'])
        except:
            db_mtime = 0
        return db_mtime >= self.gentime


def generate_asset_list():
    logging.info('Generating asset-list...')
    playlist = assets_helper.get_playlist(db_conn)
    deadline = sorted([asset['end_date'] for asset in playlist])[0] if len(playlist) > 0 else None
    logging.debug('generate_asset_list deadline: %s', deadline)

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





def load_browser():
    logging.info('Loading browser...')

    global is_pro_init, current_browser_url
    is_pro_init = get_is_pro_init()
    if not is_pro_init:
        logging.debug('Detected Pro initiation cycle.')

        # Wait for the intro file to exist (if it doesn't)
        intro_file = path.join(settings.get_configdir(), 'intro.html')
        while not path.isfile(intro_file):
            logging.debug('intro.html missing. Going to sleep.')
            sleep(0.5)

        browser_load_url = 'file://' + intro_file

    elif settings['show_splash']:
        browser_load_url = "http://%s:%s/splash_page" % (settings.get_listen_ip(), settings.get_listen_port())
    else:
        browser_load_url = black_page

    geom = [l for l in sh.xwininfo('-root').split("\n") if 'geometry' in l][0].split('y ')[1]
    browser = sh.Command('uzbl-browser')(g=geom, uri=browser_load_url, _bg=True)
    current_browser_url = browser_load_url

    logging.info('Browser loaded. Running as PID %d.' % browser.pid)

    if settings['show_splash']:
        # Show splash screen for 60 seconds.
        sleep(60)
    else:
        # Give browser some time to start (we have seen multiple uzbl running without this)
        sleep(10)

    return browser





def browser_page_has(name):
    """Return true if the given name is defined on the currently loaded browser page."""

    positive_response = "COMMAND_EXECUTED js  'typeof(%s) !== \\'undefined\\''\ntrue" % name


def browser_reload(force=False):
    """
    Reload the browser. Use to Force=True to force-reload
    """

    if not force:
        reload_command = 'reload'
    else:
        reload_command = 'reload_ign_cache'


def browser_clear():
    """Clear the browser if necessary.

    Call this function right before displaying now browser content (with feh or omx).

    When a web assset is loaded into the browser, it's not cleared after the duration but instead
    remains displayed until the next asset is ready to show. This minimises the amount of transition
    time - in the case where the next asset is also web content the browser is never cleared,
    and in other cases it's cleared as late as possible.

    """

    if current_browser_url != black_page:
        browser_url(black_page)


def browser_url(url):
    try:
        browser_page_has('life')
    except sh.ErrorReturnCode_1 as e:
        logging.exception('browser socket dead, restarting browser')
        global fifo, browser_pid
        browser_pid = load_browser().pid
        disable_browser_status()

    global current_browser_url

    if url == current_browser_url:
        logging.debug("Already showing %s, keeping it." % url)
        return
    current_browser_url = url


def disable_browser_status():
    logging.debug('Disabled status-bar in browser')


def view_image(uri, asset_id, duration):
    logging.debug('Displaying image %s for %s seconds.' % (uri, duration))

        logging.debug('Displaying uri %s for %s seconds.' % (uri, duration))

        image_tmp_page = html_templates.image_page(uri, asset_id)

        browser_url(image_tmp_page)

        sleep(int(duration))


def view_video(uri):

    ## For Raspberry Pi
    if arch == 'armv6l':
        logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % uri)

            run = omxplayer(uri, o=settings['audio_output'], _bg=True)

        # Wait until omxplayer is starting before clearing the browser. This minimises delay between
        # web and image content. Omxplayer will run on top of the browser so the delay in clearing
        # won't be visible. This minimises delay between web and video.
        browser_clear()
        run.wait()

        if run.exit_code != 0:
            logging.debug("Unclean exit: " + str(run))

        # Clean up after omxplayer
        omxplayer_logfile = HOME + 'omxplayer.log'
        if path.isfile(omxplayer_logfile):
            remove(omxplayer_logfile)

    ## For x86
    elif arch in ['x86_64', 'x86_32']:
        logging.debug('Displaying video %s. Detected x86. Using mplayer.' % uri)

            run = mplayer(uri, fs=True, nosound=True, _bg=True)

        browser_clear()
        run.wait()

        if run.exit_code != 0:
            logging.debug("Unclean exit: " + str(run))


def view_web(url, duration):
        logging.debug('Displaying url %s for %s seconds.' % (url, duration))

        browser_url(url)

        sleep(int(duration))
    else:



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

    settings_file = settings.get_configfile()
    settings_file_mtime = path.getmtime(settings_file)
    settings_file_timestamp = datetime.fromtimestamp(settings_file_mtime)

    if not last_settings_refresh or settings_file_timestamp > last_settings_refresh:
        settings.load()

    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)

    global last_setting_refresh
    last_setting_refresh = datetime.utcnow()


if __name__ == "__main__":

    HOME = getenv('HOME', '/home/pi/')

    # Install signal handlers
    signal.signal(signal.SIGUSR1, sigusr1)
    signal.signal(signal.SIGUSR2, sigusr2)

    # Before we start, reload the settings.
    reload_settings()

    global db_conn
    db_conn = db.conn(settings['database'])

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

        elif path.isfile(asset['uri']) or not url_fails(asset['uri']):

    logging.debug('Disable the browser status bar.')
    disable_browser_status()

    if not settings['verify_ssl']:
        else:
            logging.info('Asset %s at %s is not available, skipping.', asset['name'], asset['uri'])
            sleep(0.5)


    # Wait until initialized (Pro only).
    did_show_pin = False
    did_show_claimed = False
    while not get_is_pro_init():
        # Wait for the status page to fully load.
        while not browser_page_has("showPin"):
            logging.debug("Waiting for intro page to load...")
            sleep(1)

        with open(path.join(settings.get_configdir(), 'setup_status.json'), 'rb') as status_file:
            status = json.load(status_file)

        if not did_show_pin and not did_show_claimed and status.get('pin'):
            did_show_pin = True

        if not did_show_claimed and status.get('claimed'):
            did_show_claimed = True

        logging.debug('Waiting for node to be initialized.')
        sleep(1)

    # Bring up the blank page (in case there are only videos).
    logging.debug('Loading blank page.')
    view_web(black_page, 1)

    scheduler = Scheduler()

    # Infinite loop.
    logging.debug('Entering infinite loop.')
    while True:
        asset = scheduler.get_next_asset()
        logging.debug('got asset %s' % asset)

        is_up_to_date = check_update()
        logging.debug('Check update: %s' % str(is_up_to_date))

        if asset is None:
            # The playlist is empty, go to sleep.
            logging.info('Playlist is empty. Going to sleep.')
            browser_clear()
            sleep(5)

            watchdog()

            if "image" in asset["mimetype"]:
                view_image(asset['uri'], asset['asset_id'], asset["duration"])
            elif "video" in asset["mimetype"]:
                view_video(asset["uri"])
            elif "web" in asset["mimetype"]:
                view_web(asset["uri"], asset["duration"])
            else:
                print "Unknown MimeType, or MimeType missing"
