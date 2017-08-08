#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "WireLoad Inc"
__copyright__ = "Copyright 2012-2016, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

from datetime import datetime, timedelta
from functools import wraps
from hurry.filesize import size
from os import path, makedirs, statvfs, mkdir, getenv
from sh import git
import sh
from subprocess import check_output
import json
import os
import traceback
import uuid

from bottle import route, run, request, error, static_file, response
from bottle import HTTPResponse
from bottlehaml import haml_template

from lib import db
from lib import queries
from lib import assets_helper
from lib import diagnostics
from lib import backup_helper

from lib.utils import json_dump, download_video_from_youtube
from lib.utils import get_node_ip
from lib.utils import validate_url
from lib.utils import url_fails
from lib.utils import get_video_duration
from dateutil import parser as date_parser
from mimetypes import guess_type

from settings import settings, DEFAULTS, CONFIGURABLE_SETTINGS, auth_basic
from werkzeug.wrappers import Request
################################
# Utilities
################################


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
    Used in conjunction with check_update() in viewer.py.
    """

    sha_file = os.path.join(settings.get_configdir(), 'latest_screenly_sha')

    # Until this has been created by viewer.py,
    # let's just assume we're up to date.
    if not os.path.exists(sha_file):
        return True

    try:
        with open(sha_file, 'r') as f:
            latest_sha = f.read().strip()
    except:
        latest_sha = None

    if latest_sha:
        branch_sha = git('rev-parse', 'HEAD')
        return branch_sha.stdout.strip() == latest_sha

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
    context['default_duration'] = settings['default_duration']
    context['default_streaming_duration'] = settings['default_streaming_duration']
    context['use_24_hour_clock'] = settings['use_24_hour_clock']
    context['template_settings'] = {
        'imports': ['from lib.utils import template_handle_unicode'],
        'default_filters': ['template_handle_unicode'],
    }

    return haml_template(template_name, **context)


################################
# Model
################################


################################
# API
################################

def prepare_asset(request):
    req = Request(request.environ)
    data = None

    data = json.loads(req.form['model'])

    def get(key):
        val = data.get(key, '')
        if isinstance(val, unicode):
            return val.strip()
        elif isinstance(val, basestring):
            return val.strip().decode('utf-8')
        else:
            return val

    if not all([get('name'), get('uri'), get('mimetype')]):
        raise Exception("Not enough information provided. Please specify 'name', 'uri', and 'mimetype'.")

    asset = {
        'name': get('name'),
        'mimetype': get('mimetype'),
        'asset_id': get('asset_id'),
        'is_enabled': get('is_enabled'),
        'is_processing': get('is_processing'),
        'nocache': get('nocache'),
    }

    uri = get('uri')

    if uri.startswith('/'):
        if not path.isfile(uri):
            raise Exception("Invalid file path. Failed to add asset.")
    else:
        if not validate_url(uri):
            raise Exception("Invalid URL. Failed to add asset.")

    if not asset['asset_id']:
        asset['asset_id'] = uuid.uuid4().hex
        if uri.startswith('/'):
            os.rename(uri, path.join(settings['assetdir'], asset['asset_id']))
            uri = path.join(settings['assetdir'], asset['asset_id'])

    if 'youtube_asset' in asset['mimetype']:
        uri, asset['name'] = download_video_from_youtube(uri, asset['asset_id'])
        asset['mimetype'] = 'video'
        asset['is_processing'] = 1

    asset['uri'] = uri

    if "video" in asset['mimetype']:
        if asset['is_processing'] == 0:
            video_duration = get_video_duration(uri)
            if video_duration:
                asset['duration'] = int(video_duration.total_seconds())
            else:
                asset['duration'] = 'N/A'
        else:
            asset['duration'] = 'N/A'
    else:
        # Crashes if it's not an int. We want that.
        asset['duration'] = int(get('duration'))

    # parse date via python-dateutil and remove timezone info
    if get('start_date'):
        asset['start_date'] = date_parser.parse(get('start_date')).replace(tzinfo=None)
    else:
        asset['start_date'] = ""

    if get('end_date'):
        asset['end_date'] = date_parser.parse(get('end_date')).replace(tzinfo=None)
    else:
        asset['end_date'] = ""

    return asset


@route('/api/v1/assets', method="GET")
@auth_basic
def api_assets():
    with db.conn(settings['database']) as conn:
        assets = assets_helper.read(conn)
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
            return api_error(unicode(e))
    return api_view


@route('/api/v1/upload_file', method="POST")
@auth_basic
@api
def upload_file():
    req = Request(request.environ)
    file_upload = req.files.get('file_upload')
    filename = file_upload.filename
    file_path = path.join(settings['assetdir'], filename) + ".tmp"

    if 'Content-Range' in request.headers:
        range_str = request.headers['Content-Range']
        start_bytes = int(range_str.split(' ')[1].split('-')[0])
        with open(file_path, 'a') as f:
            f.seek(start_bytes)
            f.write(file_upload.read())
    else:
        file_upload.save(file_path)

    return file_path


@route('/api/v1/assets', method="POST")
@auth_basic
@api
def add_asset():
    asset = prepare_asset(request)
    if url_fails(asset['uri']):
        raise Exception("Could not retrieve file. Check the asset URL.")
    with db.conn(settings['database']) as conn:
        return assets_helper.create(conn, asset)


@route('/api/v1/assets/:asset_id', method="GET")
@auth_basic
@api
def edit_asset(asset_id):
    with db.conn(settings['database']) as conn:
        return assets_helper.read(conn, asset_id)


@route('/api/v1/assets/:asset_id', method=["PUT", "POST"])
@auth_basic
@api
def edit_asset(asset_id):
    with db.conn(settings['database']) as conn:
        return assets_helper.update(conn, asset_id, prepare_asset(request))


@route('/api/v1/assets/:asset_id', method="DELETE")
@auth_basic
@api
def remove_asset(asset_id):
    with db.conn(settings['database']) as conn:
        asset = assets_helper.read(conn, asset_id)
        try:
            if asset['uri'].startswith(settings['assetdir']):
                os.remove(asset['uri'])
        except OSError:
            pass
        assets_helper.delete(conn, asset_id)
        response.status = 204  # return an OK with no content


@route('/api/v1/assets/order', method="POST")
@auth_basic
@api
def playlist_order():
    with db.conn(settings['database']) as conn:
        assets_helper.save_ordering(conn, request.POST.get('ids', '').split(','))


@route('/api/v1/backup', method="GET")
@auth_basic
@api
def backup():
    filename = backup_helper.create_backup()
    return filename


@route('/api/v1/recover', method="POST")
@auth_basic
@api
def recover():
    req = Request(request.environ)
    file_upload = (req.files['backup_upload'])
    filename = file_upload.filename

    if guess_type(filename)[0] != 'application/x-tar':
        raise Exception("Incorrect file extension.")

    location = path.join("static", filename)
    file_upload.save(location)
    backup_helper.recover(location)
    return "Recovery successful."


################################
# Views
################################


@route('/')
@auth_basic
def viewIndex():
    player_name = settings['player_name']
    listen_ip = get_node_ip()
    return template('index', listen_ip=listen_ip, player_name=player_name)


@route('/settings', method=["GET", "POST"])
@auth_basic
def settings_page():

    context = {'flash': None}

    if request.method == "POST":
        for field, default in CONFIGURABLE_SETTINGS.items():
            value = request.POST.get(field, default)
            if isinstance(default, bool):
                value = value == 'on'
            settings[field] = value
        try:
            settings.save()
            sh.sudo('systemctl', 'kill', '--signal=SIGUSR2', 'screenly-viewer.service')
            context['flash'] = {'class': "success", 'message': "Settings were successfully saved."}
        except IOError as e:
            context['flash'] = {'class': "error", 'message': e}
        except sh.ErrorReturnCode_1 as e:
            context['flash'] = {'class': "error", 'message': e}
    else:
        settings.load()
    for field, default in DEFAULTS['viewer'].items():
        context[field] = settings[field]

    return template('settings', **context)


@route('/system_info')
@auth_basic
def system_info():
    viewlog = None
    try:
        viewlog = check_output(['sudo', 'systemctl', 'status', 'screenly-viewer.service', '-n', '20']).split('\n')
    except:
        pass

    loadavg = diagnostics.get_load_avg()['15 min']

    display_info = diagnostics.get_monitor_status()

    display_power = diagnostics.get_display_power()

    # Calculate disk space
    slash = statvfs("/")
    free_space = size(slash.f_bavail * slash.f_frsize)

    # Get uptime
    uptime_in_seconds = diagnostics.get_uptime()
    system_uptime = timedelta(seconds=uptime_in_seconds)

    # Player name for title
    player_name = settings['player_name']

    return template(
        'system_info',
        player_name=player_name,
        viewlog=viewlog,
        loadavg=loadavg,
        free_space=free_space,
        uptime=system_uptime,
        display_info=display_info,
        display_power=display_power
    )


@route('/splash_page')
def splash_page():
    try:
        my_ip = get_node_ip()
    except Exception as e:
        ip_lookup = False
        error_msg = e
    else:
        ip_lookup = True

        # If we bind on 127.0.0.1, `enable_ssl.sh` has most likely been
        # executed and we should access over SSL.
        if settings.get_listen_ip() == '127.0.0.1':
            url = 'https://{}'.format(my_ip)
        else:
            url = "http://{}:{}".format(my_ip, settings.get_listen_port())

    msg = url if url else error_msg
    return template('splash_page', ip_lookup=ip_lookup, msg=msg)


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


@route('/static_with_mime/:path#.+#', name='static')
def static_with_mime(path):
    mimetype = request.query['mime'] if 'mime' in request.query else 'auto'
    return static_file(path, root='static', mimetype=mimetype)


if __name__ == "__main__":
    # Make sure the asset folder exist. If not, create it
    if not path.isdir(settings['assetdir']):
        mkdir(settings['assetdir'])
    # Create config dir if it doesn't exist
    if not path.isdir(settings.get_configdir()):
        makedirs(settings.get_configdir())

    with db.conn(settings['database']) as conn:
        with db.cursor(conn) as cursor:
            cursor.execute(queries.exists_table)
            if cursor.fetchone() is None:
                cursor.execute(assets_helper.create_assets_table)

    run(
        host=settings.get_listen_ip(),
        port=settings.get_listen_port(),
        server='gunicorn',
        threads=2,
        timeout=20,
    )
