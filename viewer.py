#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012-2013, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__additions__ = "James Kirsop - 2013-2015"

from datetime import datetime, timedelta
from glob import glob
from os import path, getenv, remove, makedirs
from os import stat as os_stat, utime, system, kill
from random import shuffle
from requests import get as req_get, head as req_head
# from stat import S_ISFIFO
from time import sleep, time
import json
import logging
import sh
import signal
from ctypes import cdll

from settings import settings
import html_templates

from utils import url_fails

import db
import assets_helper
# Define to none to ensure we refresh
# the settings.
last_settings_refresh = None
load_screen_pid = None
current_browser_url = None
browser = None
UZBLRC = '/pisign/misc/uzbl.rc'
BLACK_PAGE = '/tmp/screenly_html/black_page.html'

# Detect the architecture and load the proper video player
from sh import omxplayer

# Used by send_to_front.
libx11 = cdll.LoadLibrary('libX11.so')

def send_to_front(name):
    """Instruct X11 to bring a window with the given name in its title to front."""

    r = [l for l in sh.xwininfo('-root', '-tree').split("\n") if name in l]
    if not len(r) == 1:
        logging.info("Unable to send window with %s in title to front - %d matches found." % (name, len(r)))
        return
    win_id = int(r[0].strip().split(" ", 2)[0], 16)

    dsp = libx11.XOpenDisplay(None)
    logging.debug("Raising %s window %X to front." % (name, win_id))
    libx11.XRaiseWindow(dsp, win_id)
    libx11.XCloseDisplay(dsp)


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

        # self.previous_asset = None

    def get_next_asset(self):
        logging.debug('get_next_asset')
        self.refresh_playlist()
        if self.nassets == 0:
            return None
        idx = self.index
        self.index = (self.index + 1) % self.nassets
        logging.debug('get_next_asset counter %d returning asset %d of %d' % (self.counter, idx + 1, self.nassets))
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1
        self.previous_asset = self.assets[idx]
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
            db_mtime = path.getmtime(settings['database'])
        except:
            db_mtime = 0
        return db_mtime >= self.gentime


def generate_asset_list():
    logging.info('Generating asset-list...')
    playlist = assets_helper.get_playlist(db_conn)
    logging.debug('Assets: %s', str(len(playlist)))
    # deadline = sorted([asset['end_date'] for asset in playlist])[0] if len(playlist) > 0 else None
    # logging.debug('generate_asset_list deadline: %s' % deadline)
    deadline = None

    if settings['shuffle_playlist']:
        shuffle(playlist)

    # return (playlist, deadline)
    return (playlist, None)


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

    asset_folder = settings['assetdir']
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


def load_browser(url=None):
    logging.info("URL: %s", url)
    global browser, current_browser_url
    logging.info('Loading browser...')

    if browser:
        logging.info('Killing previous browser instances %s', browser.pid)
        browser.process.kill()

        # Wait for the intro file to exist (if it doesn't)
        # intro_file = path.join(settings.get_configdir(), 'intro.html')
        # while not path.isfile(intro_file):
        #     logging.debug('intro.html missing. Going to sleep.')
        #     sleep(0.5)

    if not url is None:
        current_browser_url = url

    browser = sh.Command('uzbl-browser')(current_browser_url, print_events=True, config='-', _bg=True)
    logging.info('Browser loading %s. Running as PID %s.', current_browser_url, browser.pid)

    uzbl_rc = 'ssl_verify {}\n'.format('1' if settings['verify_ssl'] else '0')
    with open(HOME + UZBLRC) as f:  # load uzbl.rc
        uzbl_rc = f.read() + uzbl_rc
    browser_send(uzbl_rc)

def browser_send(command, cb=lambda _: True):
    if not (browser is None) and browser.process.alive:
        logging.debug('Browser is alive')
        while not browser.process._pipe_queue.empty():  # flush stdout
            browser.next()

        browser.process.stdin.put(command + '\n')

        while True:  # loop until cb returns True
            if cb(browser.next()):
                break
    else:
        logging.info('browser found dead, restarting')
        load_browser()


def browser_socket(command, timeout=0.5):
    """Like browser_fifo but also read back any immediate response from UZBL.

    Note that the response can be anything, including events entirely unrelated
    to the command executed.
    """

    uzbl_socket = "/tmp/uzbl_socket_%d" % browser_pid
    r = sh.socat("-t%f" % timeout, "-", "unix-connect:%s" % uzbl_socket, _in=command + "\n", _timeout=2)
    # Very spammy.
    # logging.debug("browser_socket(%r) -> %r" % (command, r))
    return r


def browser_page_has(name):
    """Return true if the given name is defined on the currently loaded browser page."""

    positive_response = "COMMAND_EXECUTED js  'typeof(%s) !== \\'undefined\\''\ntrue" % name
    # return positive_response in browser_socket("js typeof(%s) !== 'undefined'" % name)


def browser_reload(force=False):
    """
    Reload the browser. Use to Force=True to force-reload
    """

    if not force:
        reload_command = 'reload'
    else:
        reload_command = 'reload_ign_cache'

    # browser_fifo(reload_command)


def browser_clear(force=False):
    """Clear the browser if necessary. """
    browser_url(BLACK_PAGE, force=force, cb=lambda buf: 'LOAD_FINISH' in buf and BLACK_PAGE in buf)
    browser_send('js window.setimg("")', cb=lambda b: 'COMMAND_EXECUTED' in b and 'setimg' in b)


def browser_url(url, cb=lambda _: True, force=False):
    global current_browser_url
    if url == current_browser_url and not force:
        if not (browser is None) and browser.process.alive:
            logging.debug('Already showing %s, keeping it.', current_browser_url)
        else:
            logging.info('browser found dead, restarting')
            load_browser()
    else:
        current_browser_url = url
        browser_send('uri ' + current_browser_url, cb=cb)
        logging.info('current url is %s', current_browser_url)


def disable_browser_status():
    logging.debug('Disabled status-bar in browser')
    # browser_fifo('set show_status = 0')


def view_image(uri):
    logging.debug('Displaying image %s' % (uri))
    browser_clear()
    logging.debug('Image URI:'+uri)
    browser_send('js window.setimg("{0}")'.format(uri), cb=lambda b: 'COMMAND_EXECUTED' in b and 'setimg' in b)

    # if asset_is_accessible(uri):
    #     logging.debug('Image appears to be available. Proceeding.')
    #     logging.debug('Displaying uri %s for %s seconds.' % (uri, duration))
    # else:
    #     logging.debug('Received non-200 status (or file not found if local) from %s. Skipping.' % (uri))


def view_video(uri,asset_id):
    browser_clear()
    logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % uri)

    if asset_is_accessible(uri):
        run = omxplayer(uri, o=settings['audio_output'], _bg=True)
    else:
        logging.debug('Content is unaccessible. Skipping...')
        return

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


def toggle_load_screen(status=True):
    """
    Toggle the load screen. Set status to either True or False.
    """

    load_screen = HOME + 'screenly/loading.jpg'
    global load_screen_pid

    if status and path.isfile(load_screen):
        if not load_screen_pid:
            image_loader = sh.feh(load_screen, scale_down=True, borderless=True, fullscreen=True, _bg=True)
            load_screen_pid = image_loader.pid
            logging.debug("Load screen PID: %d." % load_screen_pid)
        else:
            # If we're already showing the load screen, just make sure it's on top.
            send_to_front("feh")
    elif not status and load_screen_pid:
        logging.debug("Killing load screen with PID: %d." % load_screen_pid)
        kill(load_screen_pid, signal.SIGTERM)
        load_screen_pid = None

    return load_screen_pid


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

    settings_file = settings.get_configfile()
    settings_file_mtime = path.getmtime(settings_file)
    settings_file_timestamp = datetime.fromtimestamp(settings_file_mtime)

    if not last_settings_refresh or settings_file_timestamp > last_settings_refresh:
        settings.load()

    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)

    global last_setting_refresh
    last_setting_refresh = datetime.utcnow()


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



def asset_loop(scheduler):
    # check_update()
    asset = scheduler.get_next_asset()

    if asset is None:
            # The playlist is empty, go to sleep.
            logging.info('Playlist is empty. Going to sleep.')
            toggle_load_screen(True)
            browser_clear()
            sleep(10)

    elif not url_fails(asset['uri']):
        logging.info('Showing asset %s.' % asset["name"])

        if "image" in asset["mimetype"]:
            view_image(asset['uri'])
        elif "video" in asset["mimetype"]:
            view_video(asset["uri"],asset["asset_id"])
            return
        elif "web" in asset["mimetype"]:
            browser_url(asset["uri"])
        else:
            print "Unknown MimeType, or MimeType missing"
        if "image" in asset["mimetype"] or "web" in asset["mimetype"]:
            logging.debug('Duration:'+str(asset["duration"]))
            if (not asset["duration"] == None) and (not int(asset["duration"]) == 0) :
                sleep(int(asset["duration"]))
                return
        
        logging.info('Sleeping for 30 seconds')
        sleep(30)

    else:
        logging.info('Asset {0} is not available, skipping.'.format(asset['uri']))


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
    html_templates.black_page(BLACK_PAGE)

    if settings['show_splash']:
        url = 'http://{0}:{1}/splash_page'.format(settings.get_listen_ip(), settings.get_listen_port())
        wait_for_splash_page(url)
        load_browser(url=url)
        sleep(45)
    else:
        uri = 'file://' + BLACK_PAGE
        load_browser(url=uri)

    # logging.debug('Disable the browser status bar.')
    # disable_browser_status()

    scheduler = Scheduler()

    # Infinite loop.
    logging.debug('Entering infinite loop.')
    while True:
        asset_loop(scheduler)
