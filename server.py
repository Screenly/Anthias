#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1.2"
__email__ = "vpetersson@wireload.net"

from datetime import datetime, timedelta
from dateutils import datestring
from functools import wraps
import uuid
from hurry.filesize import size
from os import path, makedirs, getloadavg, statvfs, mkdir, getenv
import os
from requests import get as req_get, head as req_head
from subprocess import check_output
import traceback
from urlparse import urlparse
import json
from uptime import uptime
from re import split as re_split
from sh import git
import ConfigParser

#from StringIO import StringIO
#from PIL import Image

from bottle import route, run, request, error, static_file, response, redirect
from bottle import HTTPResponse
from bottlehaml import haml_template

from db import connection
from utils import json_dump

from utils import get_node_ip
from settings import settings


################################
# Utilities
################################


def validate_uri(uri):
    """Simple URL verification.

    >>> validate_uri("hello")
    False
    >>> validate_uri("ftp://example.com")
    False
    >>> validate_uri("http://")
    False
    >>> validate_uri("http://wireload.net/logo.png")
    True
    >>> validate_uri("https://wireload.net/logo.png")
    True

    """

    uri_check = urlparse(uri)

    return bool(uri_check.scheme in ('http', 'https') and uri_check.netloc)


def make_json_response(obj):
    response.content_type = "application/json"
    return json_dump(obj)


def api_error(error):
    response.content_type = "application/json"
    response.status = 500
    return json_dump({'error': error})


def is_up_to_date():
    """
    Determine if there is any update available.
    Used in conjunction with check_update() in server.py
    """

    sha_file = path.join(getenv('HOME'), '.screenly', 'latest_screenly_sha')

    try:
        f = open(sha_file, 'r')
        latest_sha = f.read().strip()
        f.close()
    except:
        latest_sha = False

    if latest_sha:
        try:
            check_sha = git('branch', '--contains', latest_sha)
        except:
            check_sha = None

        if 'master' in check_sha:
            return True
        else:
            return False
    # If we weren't able to verify with remote side,
    # we'll set up_to_date to true in order to hide
    # the 'update available' message
    else:
        return True


def template(template_name, **context):
    """Screenly template response generator. Shares the
    same function signature as Bottle's template() method
    but also injects some global context."""

    # Add global contexts
    context['up_to_date'] = is_up_to_date()

    return haml_template(template_name, **context)


################################
# Model
################################

FIELDS = [
    "asset_id", "name", "uri", "start_date",
    "end_date", "duration", "mimetype"
]


def initiate_db():
    # Create config dir if it doesn't exist
    if not path.isdir(settings.configdir):
        makedirs(settings.configdir)

    c = connection.cursor()

    # Check if the asset-table exist. If it doesn't, create it.
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
    asset_table = c.fetchone()

    if not asset_table:
        c.execute("CREATE TABLE assets (asset_id TEXT, name TEXT, uri TEXT, md5 TEXT, start_date TIMESTAMP, end_date TIMESTAMP, duration TEXT, mimetype TEXT)")
        return "Initiated database."


def is_active(asset, at_time=None):
    """Accepts an asset dictionary and determines if it
    is active at the given time. If no time is specified,
    get_current_time() is used.

    >>> asset = {'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'mimetype': u'web', 'name': u'WireLoad', 'end_date': datetime(2013, 1, 19, 23, 59), 'uri': u'http://www.wireload.net', 'duration': u'5', 'start_date': datetime(2013, 1, 16, 0, 0)};

    >>> is_active(asset, datetime(2013, 1, 16, 12, 00))
    True
    >>> is_active(asset, datetime(2014, 1, 1))
    False

    """

    if not (asset['start_date'] and asset['end_date']):
        return False

    at_time = at_time or settings.get_current_time()

    return (asset['start_date'] < at_time and asset['end_date'] > at_time)


def fetch_assets(keys=FIELDS, order_by="name"):
    """Fetches all assets from the database and returns their
    data as a list of dictionaries corresponding to each asset."""
    c = connection.cursor()
    c.execute("SELECT %s FROM assets ORDER BY %s" % (", ".join(keys), order_by))
    assets = []

    for asset in c.fetchall():
        dictionary = {}
        for i in range(len(keys)):
            dictionary[keys[i]] = asset[i]
        assets.append(dictionary)

    return assets


def fetch_asset(asset_id, keys=FIELDS):
    c = connection.cursor()
    c.execute("SELECT %s FROM assets WHERE asset_id=?" % ", ".join(keys), (asset_id,))
    assets = []
    for asset in c.fetchall():
        dictionary = {}
        for i in range(len(keys)):
            dictionary[keys[i]] = asset[i]
        assets.append(dictionary)
    if len(assets):
        asset = assets[0]
        asset.update({'is_active': is_active(asset)})
        return asset


def insert_asset(asset):
    c = connection.cursor()
    c.execute(
        "INSERT INTO assets (%s) VALUES (%s)" % (", ".join(asset.keys()), ",".join(["?"] * len(asset.keys()))),
        asset.values()
    )
    connection.commit()
    asset.update({'is_active': is_active(asset)})
    return asset


def update_asset(asset_id, asset):
    del asset['asset_id']
    c = connection.cursor()
    query = "UPDATE assets SET %s=? WHERE asset_id=?" % "=?, ".join(asset.keys())
    c.execute(query, asset.values() + [asset_id])
    connection.commit()
    asset.update({'asset_id': asset_id})
    asset.update({'is_active': is_active(asset)})
    return asset


def delete_asset(asset_id):
    c = connection.cursor()
    c.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,))
    connection.commit()


################################
# API
################################

def prepare_asset(request):

    data = request.POST or request.FORM or {}

    if 'model' in data:
        data = json.loads(data['model'])

    def get(key):
        val = data.get(key, '')
        return val.strip() if isinstance(val, basestring) else val

    if all([
        get('name'),
        get('uri') or (request.files.file_upload != ""),
        get('mimetype')]):

        asset = {
            'name': get('name').decode('UTF-8'),
            'mimetype': get('mimetype'),
            'asset_id': get('asset_id'),
        }

        uri = get('uri') or False

        if not asset['asset_id']:
            asset['asset_id'] = uuid.uuid4().hex

        try:
            file_upload = request.files.file_upload
            filename = file_upload.filename
        except AttributeError:
            file_upload = None
            filename = None

        if filename and 'web' in asset['mimetype']:
            raise Exception("Invalid combination. Can't upload a web resource.")

        if uri and filename:
            raise Exception("Invalid combination. Can't select both URI and a file.")

        if uri and not uri.startswith('/'):
            if not validate_uri(uri):
                raise Exception("Invalid URL. Failed to add asset.")

            if "image" in asset['mimetype']:
                file = req_get(uri, allow_redirects=True)
            else:
                file = req_head(uri, allow_redirects=True)

            if file.status_code == 200:
                asset['uri'] = uri
                # strict_uri = file.url

            else:
                raise Exception("Could not retrieve file. Check the asset URL.")
        else:
            asset['uri'] = uri

        if filename:
            asset['uri'] = path.join(settings.asset_folder, asset['asset_id'])

            with open(asset['uri'], 'w') as f:
                while True:
                    chunk = file_upload.file.read(1024)
                    if not chunk:
                        break
                    f.write(chunk)


        if "video" in asset['mimetype']:
            asset['duration'] = "N/A"
        else:
            # crashes if it's not an int. we want that.
            asset['duration'] = int(get('duration'))

        if get('start_date'):
            asset['start_date'] = datetime.strptime(get('start_date').split(".")[0], "%Y-%m-%dT%H:%M:%S")
        else:
            asset['start_date'] = ""

        if get('end_date'):
            asset['end_date'] = datetime.strptime(get('end_date').split(".")[0], "%Y-%m-%dT%H:%M:%S")
        else:
            asset['end_date'] = ""

        if not asset['asset_id']:
            raise Exception

        if not asset['uri']:
            raise Exception

        return asset
    else:
        raise Exception("Not enough information provided. Please specify 'name', 'uri', and 'mimetype'.")


@route('/api/assets', method="GET")
def api_assets():
    assets = fetch_assets()
    for asset in assets:
        asset['is_active'] = is_active(asset)
    return make_json_response(assets)


# api view decorator. handles errors
def api(view):
    @wraps(view)
    def api_view(*args, **kwargs):
        try:
            return make_json_response(view(*args, **kwargs))
        except HTTPResponse:
            raise
        except Exception as e:
            traceback.print_exc()
            return api_error(str(e))
    return api_view


@route('/api/assets', method="POST")
@api
def add_asset():
    return insert_asset(prepare_asset(request))


@route('/api/assets/:asset_id', method="GET")
@api
def edit_asset(asset_id):
    return fetch_asset(asset_id)


@route('/api/assets/:asset_id', method=["PUT", "POST"])
@api
def edit_asset(asset_id):
    return update_asset(asset_id, prepare_asset(request))


@route('/api/assets/:asset_id', method="DELETE")
@api
def remove_asset(asset_id):
    asset = fetch_asset(asset_id)
    try:
        if asset['uri'].startswith(settings.asset_folder):
            os.remove(asset['uri'])
    except OSError:
        pass
    delete_asset(asset_id)
    response.status = 204  # return an OK with no content


################################
# Views
################################

@route('/')
def viewIndex():
    return template('index')


@route('/settings', method=["GET", "POST"])
def settings_page():

    config = ConfigParser.ConfigParser()
    conf_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')
    config.read(conf_file)
    context = {'flash': None}

    if request.method == "POST":
        config.set("viewer", "show_splash", str(request.POST.get('show_splash', 'off') == 'on'))
        config.set("viewer", "audio_output", request.POST.get('audio_output', 'hdmi'))
        config.set("viewer", "shuffle_playlist", str(request.POST.get('shuffle_playlist', 'off') == 'on'))

        try:
            # Write new settings to disk.
            with open(conf_file, "w") as settings_file:
                config.write(settings_file)
            settings.load_settings()  # reload the new settings into memory
            context['flash'] = {'class': "success", 'message': "Settings were successfully saved."}
        except Exception as e:
            context['flash'] = {'class': "error", 'message': e}

    context['show_splash'] = config.get('viewer', 'show_splash')
    context['audio_output'] = config.get('viewer', 'audio_output')
    context['shuffle_playlist'] = config.get('viewer', 'shuffle_playlist')

    return template('settings', **context)


@route('/system_info')
def system_info():
    viewer_log_file = '/tmp/screenly_viewer.log'
    if path.exists(viewer_log_file):
        viewlog = check_output(['tail', '-n', '20', viewer_log_file]).split('\n')
    else:
        viewlog = ["(no viewer log present -- is only the screenly server running?)\n"]

    # Get load average from last 15 minutes and round to two digits.
    loadavg = round(getloadavg()[2], 2)

    try:
        run_tvservice = check_output(['tvservice', '-s'])
        display_info = re_split('\||,', run_tvservice.strip('state:'))
    except:
        display_info = False

    # Calculate disk space
    slash = statvfs("/")
    free_space = size(slash.f_bavail * slash.f_frsize)

    # Get uptime
    uptime_in_seconds = uptime()
    system_uptime = timedelta(seconds=uptime_in_seconds)

    return template('system_info', viewlog=viewlog, loadavg=loadavg, free_space=free_space, uptime=system_uptime, display_info=display_info)


@route('/splash_page')
def splash_page():
    my_ip = get_node_ip()
    if my_ip:
        ip_lookup = True
        url = "http://{}:{}".format(my_ip, settings.listen_port)
    else:
        ip_lookup = False
        url = "Unable to look up your installation's IP address."

    return template('splash_page', ip_lookup=ip_lookup, url=url)


@error(403)
def mistake403(code):
    return 'The parameter you passed has the wrong format!'


@error(404)
def mistake404(code):
    return 'Sorry, this page does not exist!'


################################
# Static
################################

@route('/static/:path#.+#', name='static')
def static(path):
    return static_file(path, root='static')


if __name__ == "__main__":
    # Make sure the asset folder exist. If not, create it
    if not path.isdir(settings.asset_folder):
        mkdir(settings.asset_folder)

    initiate_db()

    run(host=settings.listen_ip, port=settings.listen_port, reloader=True)
