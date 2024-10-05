#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from future import standard_library
__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2023, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

from os import getenv, makedirs, mkdir, path, stat

from flask import (
    Flask,
    make_response,
    request,
    send_from_directory,
    url_for,
)
from flask_cors import CORS
from flask_restful_swagger_2 import Api
from flask_swagger_ui import get_swaggerui_blueprint
from gunicorn.app.base import Application

from api.views.v1 import (
    Asset,
    AssetContent,
    Assets,
    AssetsControl,
    Backup,
    FileAsset,
    Info,
    PlaylistOrder,
    Reboot,
    Recover,
    Shutdown,
    ViewerCurrentAsset,
)
from api.views.v1_1 import (
    AssetV1_1,
    AssetsV1_1,
)
from api.views.v1_2 import (
    AssetV1_2,
    AssetsV1_2,
)


from lib import assets_helper
from lib import db
from lib import queries

from lib.auth import authorized
from lib.utils import (
    json_dump,
    get_node_ip,
    connect_to_redis,
)
from anthias_app.views import anthias_app_bp
from settings import LISTEN, PORT, settings


standard_library.install_aliases()

HOME = getenv('HOME')

app = Flask(__name__)
app.register_blueprint(anthias_app_bp)

CORS(app)
api = Api(app, api_version="v1", title="Anthias API")

r = connect_to_redis()


################################
# Utilities
################################


@api.representation('application/json')
def output_json(data, code, headers=None):
    response = make_response(json_dump(data), code)
    response.headers.extend(headers or {})
    return response


################################
# API
################################


api.add_resource(Assets, '/api/v1/assets')
api.add_resource(Asset, '/api/v1/assets/<asset_id>')
api.add_resource(AssetsV1_1, '/api/v1.1/assets')
api.add_resource(AssetV1_1, '/api/v1.1/assets/<asset_id>')
api.add_resource(AssetsV1_2, '/api/v1.2/assets')
api.add_resource(AssetV1_2, '/api/v1.2/assets/<asset_id>')
api.add_resource(AssetContent, '/api/v1/assets/<asset_id>/content')
api.add_resource(FileAsset, '/api/v1/file_asset')
api.add_resource(PlaylistOrder, '/api/v1/assets/order')
api.add_resource(Backup, '/api/v1/backup')
api.add_resource(Recover, '/api/v1/recover')
api.add_resource(AssetsControl, '/api/v1/assets/control/<command>')
api.add_resource(Info, '/api/v1/info')
api.add_resource(Reboot, '/api/v1/reboot')
api.add_resource(Shutdown, '/api/v1/shutdown')
api.add_resource(ViewerCurrentAsset, '/api/v1/viewer_current_asset')

try:
    my_ip = get_node_ip()
except Exception:
    pass
else:
    SWAGGER_URL = '/api/docs'
    API_URL = "/api/swagger.json"

    swaggerui_blueprint = get_swaggerui_blueprint(
        SWAGGER_URL,
        API_URL,
        config={
            'app_name': "Anthias API"
        }
    )
    app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)


@app.errorhandler(403)
def mistake403(code):
    return 'The parameter you passed has the wrong format!'


@app.errorhandler(404)
def mistake404(code):
    return 'Sorry, this page does not exist!'


################################
# Static
################################


@app.context_processor
def override_url_for():
    return dict(url_for=dated_url_for)


def dated_url_for(endpoint, **values):
    if endpoint == 'static':
        filename = values.get('filename', None)
        if filename:
            file_path = path.join(app.root_path,
                                  endpoint, filename)
            if path.isfile(file_path):
                values['q'] = int(stat(file_path).st_mtime)
    return url_for(endpoint, **values)


@app.route('/static_with_mime/<string:path>')
@authorized
def static_with_mime(path):
    mimetype = request.args['mime'] if 'mime' in request.args else 'auto'
    return send_from_directory(
        directory='static', filename=path, mimetype=mimetype)


@app.before_first_request
def main():
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


def is_development():
    return getenv('ENVIRONMENT', '') == 'development'


if __name__ == "__main__" and not is_development():
    config = {
        'bind': '{}:{}'.format(LISTEN, PORT),
        'threads': 2,
        'timeout': 20
    }

    class GunicornApplication(Application):
        def init(self, parser, opts, args):
            return config

        def load(self):
            return app

    GunicornApplication().run()
