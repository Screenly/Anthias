#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "GPL"
__version__ = "0.1"
__email__ = "vpetersson@wireload.net"

import subprocess, mimetypes, os, sqlite3, shutil, platform
import html_templates
from datetime import datetime
from time import sleep
import logging
import glob, stat

# Define settings
configdir = os.getenv("HOME") + "/.screenly2/"
database = configdir + "screenly2.db"
nodetype = "standalone"

# Initiate logging
logging.basicConfig(level=logging.INFO,
                    filename='/tmp/screenly_viewer.log',
                    format='%(asctime)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')

arch = platform.machine()

def time_lookup():
    if nodetype == "standalone":
        return datetime.now()
    elif nodetype == "managed":
        return datetime.utcnow()
                        
def generate_asset_list():
    logging.info('Generating asset-list...')
    conn = sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
    c = conn.cursor()
    c.execute("SELECT * FROM assets ORDER BY name")
    query = c.fetchall()

    playlist = []
    for asset in query:
        asset_id = asset[0]  
        name = asset[1]
        filename = asset[2]
        uri = asset[3]
        md5 = asset[4]
        start_date = asset[5]
        end_date = asset[6]
        duration = asset[7]
        mimetype = asset[8]

        if (start_date and end_date) and (start_date < time_lookup() and end_date > time_lookup()):
            playlist.append({"name" : name, "uri" : uri, "duration" : duration, "mimetype" : mimetype})
    return playlist
    
def load_browser():
    logging.info('Loading browser...')
    browser_bin = "uzbl-browser"
    browser_resolution = "1920x1080"
    browser_load_url = "http://127.0.0.1:8080/splash_page"
    browser_args = [browser_bin, "--geometry=" + browser_resolution, "--uri=" + browser_load_url]
    browser = subprocess.Popen(browser_args)
    
    logging.info('Browser loaded. Running as PID %d.' % browser.pid)

    # Show splash screen for 60 seconds.
    sleep(60)

    return browser

def get_fifo():
    candidates = glob.glob('/tmp/uzbl_fifo_*')
    for file in candidates:
        if stat.S_ISFIFO(os.stat(file).st_mode):
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
    ## For Raspberry Pi
    if arch == "armv6l":
        logging.debug('Displaying video %s. Detected Raspberry Pi. Using omxplayer.' % video)
        omxplayer = "omxplayer"
        omxplayer_args = [omxplayer, "-o", "hdmi", "-w", str(video)]
        run = subprocess.call(omxplayer_args, stdout=True)
        logging.debug(run)

        if run != 0:
            logging.debug("Unclean exit: " + str(run))

        # Clean up after omxplayer
        omxplayer_logfile = os.getenv("HOME") + omxplayer.log
        os.remove(omxplayer_logfile)

    ## For x86
    elif arch == "x86_64" or arch == "x86_32":
        logging.debug('Displaying video %s. Detected x86. Using mplayer.' % video)
        mplayer = "mplayer"
        run = subprocess.call([mplayer, "-fs", "-nosound", str(video) ], stdout=False)
        if run != 0:
            logging.debug("Unclean exit: " + str(run))

def view_web(url, duration):
    logging.debug('Displaying url %s for %s seconds.' % (url, duration))
    f = open(fifo, 'a')
    f.write('set uri = %s\n' % url)
    f.close()
    
    sleep(int(duration))
    
    f = open(fifo, 'a')
    f.write('set uri = %s\n' % black_page)
    f.close()


# Set up HTML templates
black_page = html_templates.black_page()

# Fire up the browser
run_browser = load_browser()
browser_pid = run_browser.pid
fifo = get_fifo()

# Infinit loop. 
# Break every 5th run to refresh database

while True:
    assets = generate_asset_list()
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