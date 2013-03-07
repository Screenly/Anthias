#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1"
__email__ = "vpetersson@wireload.net"

from datetime import datetime, timedelta
from glob import glob
from os import path, getenv, remove, makedirs
from os import stat as os_stat, utime, system
from platform import machine
from random import shuffle
from requests import get as req_get
from stat import S_ISFIFO
from subprocess import Popen, call
from time import sleep, time
import logging
import signal

from settings import settings
import html_templates

from utils import validate_url
from utils import url_fails

import db
import assets_helper
# Define to none to ensure we refresh
# the settings.
last_settings_refresh = None


#This is the signal handler for SIGUSR1
#After this returns all sleep() calls are aborted
#Since we also kill omxplayer it means we abort 
#displaying the current asset
def sigusr1(signum, frame):
	logging.info("Skip current asset")
        system("killall omxplayer.bin")

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
    deadline = sorted([ass['end_date'] for ass in playlist])[0]
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


def load_browser():
    logging.info('Loading browser...')
    browser_bin = "uzbl-browser"
    browser_resolution = settings['resolution']

    if settings['show_splash']:
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
    candidates = glob('/tmp/uzbl_fifo_*')
    for file in candidates:
        if S_ISFIFO(os_stat(file).st_mode):
            return file
    return None


def browser_set(set_data):
    f = open(fifo, 'a')
    f.write('set %s\n' % set_data)
    f.close()


def browser_url(url):
    browser_set('uri = %s' % url)


def disable_browser_status():
    logging.debug('Disabled status-bar in browser')
    browser_set('show_status = 0')


def view_image(image, asset_id, duration):
    logging.debug('Displaying image %s for %s seconds.' % (image, duration))
    url = html_templates.image_page(image, asset_id)
    browser_url(url)

    sleep(int(duration))

    browser_url(black_page)


def view_video(video):
    arch = machine()

    ## For Raspberry Pi
    if arch == "armv6l":
        logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % video)
        omxplayer = "omxplayer"
        omxplayer_args = [omxplayer, "-o", settings['audio_output'], str(video)]
        run = call(omxplayer_args, stdout=True)
        logging.debug(run)

        if run != 0:
            logging.debug("Unclean exit: " + str(run))

        # Clean up after omxplayer
        omxplayer_logfile = path.join(getenv('HOME'), 'omxplayer.log')
        if path.isfile(omxplayer_logfile):
            remove(omxplayer_logfile)

    ## For x86
    elif arch in ['x86_64', 'x86_32']:
        logging.debug('Displaying video %s. Detected x86. Using mplayer.' % video)
        mplayer = "mplayer"
        run = call([mplayer, "-fs", "-nosound", str(video)], stdout=False)
        if run != 0:
            logging.debug("Unclean exit: " + str(run))


def view_web(url, duration):
    # If local web page, check if the file exist. If remote, check if it is
    # available.
    if (html_folder in url and path.exists(url)):
        web_resource = 200
    else:
        web_resource = req_get(url).status_code

    if web_resource == 200:
        logging.debug('Web content appears to be available. Proceeding.')
        logging.debug('Displaying url %s for %s seconds.' % (url, duration))
        browser_url(url)

        sleep(int(duration))

        browser_url(url)
    else:
        logging.debug('Received non-200 status (or file not found if local) from %s. Skipping.' % (url))


def check_update():
    """
    Check if there is a later version of Screenly-OSE
    available. Only do this update once per day.
    """

    sha_file = path.join(getenv('HOME'), '.screenly', 'latest_screenly_sha')

    try:
        sha_file_mtime = path.getmtime(sha_file)
        last_update = datetime.fromtimestamp(sha_file_mtime)
    except:
        last_update = None

    if last_update is None or last_update < (datetime.now() - timedelta(days=1)):
        latest_sha = req_get('http://stats.screenlyapp.com/latest')
        if latest_sha.status_code == 200:
            with open(sha_file, 'w') as f:
                f.write(latest_sha.content.strip())


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

    global last_setting_refresh
    last_setting_refresh = datetime.utcnow()


if __name__ == "__main__":

    #Install signal handler
    signal.signal(signal.SIGUSR1, sigusr1)


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

    # Bring up the blank page (in case there are only videos).
    logging.debug('Loading blank page.')
    view_web(black_page, 1)

    logging.debug('Disable the browser status bar.')
    disable_browser_status()

    scheduler = Scheduler()

    # Infinite loop.
    logging.debug('Entering infinite loop.')
    while True:
        asset = scheduler.get_next_asset()
        logging.debug('got asset' + str(asset))

        check_update()

        if asset is None:
            # The playlist is empty, go to sleep.
            logging.info('Playlist is empty. Going to sleep.')
            sleep(5)
        elif not url_fails(asset['uri']):
            logging.info('Showing asset %s.' % asset["name"])

            watchdog()

            if "image" in asset["mimetype"]:
                img_uri = asset['uri']
                if asset['nocache'] == 1 and validate_url(img_uri):
                    img_uri += '?_nocache=' + str(time())
                view_image(img_uri, asset["asset_id"], asset["duration"])
            elif "video" in asset["mimetype"]:
                view_video(asset["uri"])
            elif "web" in asset["mimetype"]:
                view_web(asset["uri"], asset["duration"])
            else:
                print "Unknown MimeType, or MimeType missing"
        else:
            logging.info('Asset {0} is not available, skipping.'.format(asset['uri']))
