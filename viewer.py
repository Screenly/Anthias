#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1"
__email__ = "vpetersson@wireload.net"

import sqlite3, ConfigParser
from sys import exit
from requests import get 
from platform import machine 
from os import path, getenv, remove, makedirs
from os import stat as os_stat
from subprocess import Popen, call 
import html_templates
from datetime import datetime
from time import sleep
import logging
from glob import glob
from stat import S_ISFIFO

# Initiate logging
logging.basicConfig(level=logging.INFO,
                    filename='/tmp/screenly_viewer.log',
                    format='%(asctime)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')

# Get config file
config = ConfigParser.ConfigParser()
conf_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')
if not path.isfile(conf_file):
    logging.info('Config-file missing.')
    exit(1)
else:
    logging.debug('Reading config-file...')
    config.read(conf_file)

def time_lookup():
    if nodetype == "standalone":
        return datetime.now()
    elif nodetype == "managed":
        return datetime.utcnow()

def str_to_bol(string):
    if 'true' in string.lower():
        return True
    else:
        return False

def generate_asset_list():
    logging.info('Generating asset-list...')
    conn = sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
    c = conn.cursor()
    c.execute("SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets ORDER BY name")
    query = c.fetchall()

    playlist = []
    for asset in query:
        asset_id = asset[0]  
        name = asset[1].encode('ascii', 'ignore')
        uri = asset[2]
        md5 = asset[3]
        start_date = asset[4]
        end_date = asset[5]
        duration = asset[6]
        mimetype = asset[7]

        if (start_date and end_date) and (start_date < time_lookup() and end_date > time_lookup()):
            playlist.append({"name" : name, "uri" : uri, "duration" : duration, "mimetype" : mimetype})
    
    if shuffle_playlist:
        from random import shuffle
        shuffle(playlist)
    
    return playlist
    
def load_browser():
    logging.info('Loading browser...')
    browser_bin = "uzbl-browser"
    browser_resolution = resolution

    if show_splash:
        browser_load_url = "http://127.0.0.1:8080/splash_page"
    else:
        browser_load_url = black_page

    browser_args = [browser_bin, "--geometry=" + browser_resolution, "--uri=" + browser_load_url]
    browser = Popen(browser_args)
    
    logging.info('Browser loaded. Running as PID %d.' % browser.pid)

    # Show splash screen for 60 seconds.
    sleep(60)

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
        omxplayer_args = [omxplayer, "-o", audio_output, "-w", str(video)]
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

# Get config values
configdir = path.join(getenv('HOME'), config.get('main', 'configdir'))
database = path.join(getenv('HOME'), config.get('main', 'database'))
nodetype = config.get('main', 'nodetype')
show_splash = str_to_bol(config.get('viewer', 'show_splash'))
audio_output = config.get('viewer', 'audio_output')
shuffle_playlist = str_to_bol(config.get('viewer', 'shuffle_playlist'))

try:
    resolution = shuffle_playlist = config.get('viewer', 'resolution')
except:
    resolution = '1920x1080'

logging.debug('Starting viewer.py')

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

# Infinit loop. 
# Break every 5th run to refresh database

while True:
    logging.debug('Entering infinite loop.')

    # Bring up the blank page (in case there are only videos).
    logging.debug('Loading blank page.')
    view_web(black_page, 1)

    assets = generate_asset_list()

    logging.debug('Disable the browser status bar')
    disable_browser_status()

    # If the playlist is empty, go to sleep.
    if len(assets) == 0:
        logging.info('Playlist is empty. Going to sleep.')
        sleep(5)
    else:
        counter = 1
        while counter <= 5:
            logging.debug('Run counter: %d' % counter)
            for asset in assets:
                if "image" in asset["mimetype"]:
                    view_image(asset["uri"], asset["name"], asset["duration"])
                elif "video" in asset["mimetype"]:
                    view_video(asset["uri"])
                elif "web" in asset["mimetype"]:
                    view_web(asset["uri"], asset["duration"])
                else:
                    print "Unknown MimeType, or MimeType missing"
            counter += 1
