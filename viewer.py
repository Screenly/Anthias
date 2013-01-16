#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1"
__email__ = "vpetersson@wireload.net"

import sqlite3
from Config import Config
from requests import get 
from platform import machine 
from os import path, getenv, remove, makedirs
from os import stat as os_stat, utime
from subprocess import Popen, call 
import html_templates
from time import sleep, time
import logging
from glob import glob
from stat import S_ISFIFO

# Initiate logging
logging.basicConfig(level=logging.INFO,
                    filename='/tmp/screenly_viewer.log',
                    format='%(asctime)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')

# Silence urllib info messages ('Starting new HTTP connection')
# that are triggered by the remote url availability check in view_web
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.WARNING)

logging.debug('Starting viewer.py')

# Get config
config = Config(logging.debug)

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
        logging.debug('get_next_asset counter %d returning asset %d of %d' % (self.counter, idx+1, self.nassets))
        if config.shuffle_playlist and self.index == 0:
            self.counter += 1
        return self.assets[idx]

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = config.time_lookup()
        logging.debug('refresh: counter: (%d) deadline (%s) timecur (%s)' % (self.counter, self.deadline, time_cur))
        if self.dbisnewer():
            self.update_playlist()
        elif config.shuffle_playlist and self.counter >= 5:
            self.update_playlist()
        elif self.deadline != None and self.deadline <= time_cur:
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
            db_mtime = path.getmtime(config.database)
        except:
            db_mtime = 0
        return db_mtime >= self.gentime

def generate_asset_list():
    logging.info('Generating asset-list...')
    conn = sqlite3.connect(config.database, detect_types=sqlite3.PARSE_DECLTYPES)
    c = conn.cursor()
    c.execute("SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets ORDER BY name")
    query = c.fetchall()

    playlist = []
    time_cur = config.time_lookup()
    deadline = None
    for asset in query:
        asset_id = asset[0]  
        name = asset[1].encode('ascii', 'ignore')
        uri = asset[2]
        md5 = asset[3]
        start_date = asset[4]
        end_date = asset[5]
        duration = asset[6]
        mimetype = asset[7]

        logging.debug('generate_asset_list: %s: start (%s) end (%s)' % (name, start_date, end_date))
        if (start_date and end_date) and (start_date < time_cur and end_date > time_cur):
            playlist.append({"name" : name, "uri" : uri, "duration" : duration, "mimetype" : mimetype})
        if (start_date and end_date) and (start_date < time_cur and end_date > time_cur):
            if deadline == None or end_date < deadline:
               deadline = end_date
        if (start_date and end_date) and (start_date > time_cur and end_date > start_date):
            if deadline == None or start_date < deadline:
               deadline = start_date

    logging.debug('generate_asset_list deadline: %s' % deadline)

    if config.shuffle_playlist:
        from random import shuffle
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
        utime(watchdog,None)

def load_browser():
    logging.info('Loading browser...')
    browser_bin = "uzbl-browser"

    if config.show_splash:
        browser_load_url = config.get_viewer_baseurl() + '/splash_page'
    else:
        browser_load_url = black_page

    browser_args = [browser_bin, "--geometry=" + config.resolution, "--uri=" + browser_load_url]
    browser = Popen(browser_args)
    
    logging.info('Browser loaded. Running as PID %d.' % browser.pid)

    if config.show_splash:
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
        else:
            return None    
    
def disable_browser_status():
    logging.debug('Disabled status-bar in browser')
    f = open(fifo, 'a')
    f.write('set show_status = 0\n')
    f.close()


def view_image(image, name, duration):
    logging.debug('Displaying image %s for %s seconds.' % (image, duration))
    url = html_templates.image_page(image, name)
    f = open(fifo, 'a')
    f.write('set uri = %s\n' % url)
    f.close()
    
    sleep(int(duration))
    
    f = open(fifo, 'a')
    f.write('set uri = %s\n' % black_page)
    f.close()
    
def view_video(video):
    arch = machine()

    ## For Raspberry Pi
    if arch == "armv6l":
        logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % video)
        omxplayer = "omxplayer"
        omxplayer_args = [omxplayer, "-o", config.audio_output, "-w", str(video)]
        run = call(omxplayer_args, stdout=True)
        logging.debug(run)

        if run != 0:
            logging.debug("Unclean exit: " + str(run))

        # Clean up after omxplayer
        omxplayer_logfile = path.join(getenv('HOME'), 'omxplayer.log')
        if path.isfile(omxplayer_logfile):
            remove(omxplayer_logfile)

    ## For x86
    elif arch == "x86_64" or arch == "x86_32":
        logging.debug('Displaying video %s. Detected x86. Using mplayer.' % video)
        mplayer = "mplayer"
        run = call([mplayer, "-fs", "-nosound", str(video) ], stdout=False)
        if run != 0:
            logging.debug("Unclean exit: " + str(run))

def view_web(url, duration):

    # If local web page, check if the file exist. If remote, check if it is
    # available.
    if (html_folder in url and path.exists(url)):
        web_resource = 200
    else:
        web_resource = get(url).status_code

    if web_resource == 200:
        logging.debug('Web content appears to be available. Proceeding.')  
        logging.debug('Displaying url %s for %s seconds.' % (url, duration))
        f = open(fifo, 'a')
        f.write('set uri = %s\n' % url)
        f.close()
    
        sleep(int(duration))
    
        f = open(fifo, 'a')
        f.write('set uri = %s\n' % black_page)
        f.close()
    else: 
        logging.debug('Received non-200 status (or file not found if local) from %s. Skipping.' % (url))
        pass

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

logging.debug('Disable the browser status bar')
disable_browser_status()

scheduler = Scheduler()

# Infinit loop. 
logging.debug('Entering infinite loop.')
while True:

    asset = scheduler.get_next_asset()
    logging.debug('got asset'+str(asset))

    if asset == None:
        # The playlist is empty, go to sleep.
        logging.info('Playlist is empty. Going to sleep.')
        sleep(5)
    else:
        logging.info('show asset %s' % asset["name"])

        watchdog()

        if "image" in asset["mimetype"]:
            view_image(asset["uri"], asset["name"], asset["duration"])
        elif "video" in asset["mimetype"]:
            view_video(asset["uri"])
        elif "web" in asset["mimetype"]:
            view_web(asset["uri"], asset["duration"])
        else:
            print "Unknown MimeType, or MimeType missing"
