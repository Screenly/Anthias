import uuid

from base64 import b64encode
from flask import request
from flask_restful_swagger_2 import Resource, swagger
from mimetypes import guess_type, guess_extension
from os import path, remove, statvfs
from werkzeug.wrappers import Request

from api.helpers import (
    AssetModel,
    AssetContentModel,
    api_response,
    prepare_asset,
)
from celery_tasks import shutdown_anthias, reboot_anthias
from hurry.filesize import size
from lib import (
    db,
    diagnostics,
    assets_helper,
    backup_helper,
)
from lib.auth import authorized
from lib.github import is_up_to_date
from lib.utils import connect_to_redis, url_fails
from settings import (
    settings,
    ZmqCollector,
    ZmqPublisher,
)


r = connect_to_redis()


class Assets(Resource):
    method_decorators = [authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'List of assets',
                'schema': {
                    'type': 'array',
                    'items': AssetModel

                }
            }
        }
    })
    def get(self):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            return assets

    @api_response
    @swagger.doc({
        'parameters': [
            {
                'name': 'model',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Yes, that is just a string of JSON not JSON itself it will
                    be parsed on the other end.

                    Content-Type: application/x-www-form-urlencoded
                    model: "{
                        "name": "Website",
                        "mimetype": "webpage",
                        "uri": "http://example.com",
                        "is_active": 0,
                        "start_date": "2017-02-02T00:33:00.000Z",
                        "end_date": "2017-03-01T00:33:00.000Z",
                        "duration": "10",
                        "is_enabled": 0,
                        "is_processing": 0,
                        "nocache": 0,
                        "play_order": 0,
                        "skip_asset_check": 0
                    }"
                    '''
            }
        ],
        'responses': {
            '201': {
                'description': 'Asset created',
                'schema': AssetModel
            }
        }
    })
    def post(self):
        asset = prepare_asset(request)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            return assets_helper.create(conn, asset), 201


class Asset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset',
                'schema': AssetModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            },
            {
                'name': 'model',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Content-Type: application/x-www-form-urlencoded
                    model: "{
                        "asset_id": "793406aa1fd34b85aa82614004c0e63a",
                        "name": "Website",
                        "mimetype": "webpage",
                        "uri": "http://example.com",
                        "is_active": 0,
                        "start_date": "2017-02-02T00:33:00.000Z",
                        "end_date": "2017-03-01T00:33:00.000Z",
                        "duration": "10",
                        "is_enabled": 0,
                        "is_processing": 0,
                        "nocache": 0,
                        "play_order": 0,
                        "skip_asset_check": 0
                    }"
                    '''
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def put(self, asset_id):
        with db.conn(settings['database']) as conn:
            return assets_helper.update(conn, asset_id, prepare_asset(request))

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            },
        ],
        'responses': {
            '204': {
                'description': 'Deleted'
            }
        }
    })
    def delete(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return '', 204  # return an OK with no content


class FileAsset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'file_upload',
                'type': 'file',
                'in': 'formData',
                'description': 'File to be sent'
            }
        ],
        'responses': {
            '200': {
                'description': 'File path',
                'schema': {
                    'type': 'string'
                }
            }
        }
    })
    def post(self):
        req = Request(request.environ)
        file_upload = req.files.get('file_upload')
        filename = file_upload.filename
        file_type = guess_type(filename)[0]

        if not file_type:
            raise Exception("Invalid file type.")

        if file_type.split('/')[0] not in ['image', 'video']:
            raise Exception("Invalid file type.")

        file_path = path.join(
            settings['assetdir'],
            uuid.uuid5(uuid.NAMESPACE_URL, filename).hex) + ".tmp"

        if 'Content-Range' in request.headers:
            range_str = request.headers['Content-Range']
            start_bytes = int(range_str.split(' ')[1].split('-')[0])
            with open(file_path, 'ab') as f:
                f.seek(start_bytes)
                f.write(file_upload.read())
        else:
            file_upload.save(file_path)

        return {'uri': file_path, 'ext': guess_extension(file_type)}


class PlaylistOrder(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'ids',
                'in': 'formData',
                'type': 'string',
                'description':
                    '''
                    Content-Type: application/x-www-form-urlencoded
                    ids: "793406aa1fd34b85aa82614004c0e63a,1c5cfa719d1f4a9abae16c983a18903b,9c41068f3b7e452baf4dc3f9b7906595"
                    comma separated ids
                    '''  # noqa: E501
            },
        ],
        'responses': {
            '204': {
                'description': 'Sorted'
            }
        }
    })
    def post(self):
        with db.conn(settings['database']) as conn:
            assets_helper.save_ordering(
                conn, request.form.get('ids', '').split(','))


class Backup(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Backup filename',
                'schema': {
                    'type': 'string'
                }
            }
        }
    })
    def post(self):
        filename = backup_helper.create_backup(name=settings['player_name'])
        return filename, 201


class Recover(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'backup_upload',
                'type': 'file',
                'in': 'formData'
            }
        ],
        'responses': {
            '200': {
                'description': 'Recovery successful'
            }
        }
    })
    def post(self):
        publisher = ZmqPublisher.get_instance()
        req = Request(request.environ)
        file_upload = (req.files['backup_upload'])
        filename = file_upload.filename

        if guess_type(filename)[0] != 'application/x-tar':
            raise Exception("Incorrect file extension.")
        try:
            publisher.send_to_viewer('stop')
            location = path.join("static", filename)
            file_upload.save(location)
            backup_helper.recover(location)
            return "Recovery successful."
        finally:
            publisher.send_to_viewer('play')


class Reboot(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Reboot system'
            }
        }
    })
    def post(self):
        reboot_anthias.apply_async()
        return '', 200


class Shutdown(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Shutdown system'
            }
        }
    })
    def post(self):
        shutdown_anthias.apply_async()
        return '', 200


class Info(Resource):
    method_decorators = [api_response, authorized]

    def get(self):
        # Calculate disk space
        slash = statvfs("/")
        free_space = size(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return {
            'loadavg': diagnostics.get_load_avg()['15 min'],
            'free_space': free_space,
            'display_power': display_power,
            'up_to_date': is_up_to_date()
        }


class AssetsControl(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'command',
                'type': 'string',
                'in': 'path',
                'description':
                    '''
                    Control commands:
                    next - show next asset
                    previous - show previous asset
                    asset&asset_id - show asset with `asset_id` id
                    '''
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset switched'
            }
        }
    })
    def get(self, command):
        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer(command)
        return "Asset switched"


class AssetContent(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset'
            }
        ],
        'responses': {
            '200': {
                'description':
                    '''
                    The content of the asset.

                    'type' can either be 'file' or 'url'.

                    In case of a file, the fields 'mimetype', 'filename', and
                    'content'  will be present. In case of a URL, the field
                    'url' will be present.
                    ''',
                'schema': AssetContentModel
            }
        }
    })
    def get(self, asset_id):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)

        if isinstance(asset, list):
            raise Exception('Invalid asset ID provided')

        if path.isfile(asset['uri']):
            filename = asset['name']

            with open(asset['uri'], 'rb') as f:
                content = f.read()

            mimetype = guess_type(filename)[0]
            if not mimetype:
                mimetype = 'application/octet-stream'

            result = {
                'type': 'file',
                'filename': filename,
                'content': b64encode(content).decode(),
                'mimetype': mimetype
            }
        else:
            result = {
                'type': 'url',
                'url': asset['uri']
            }

        return result


class ViewerCurrentAsset(Resource):
    method_decorators = [api_response, authorized]

    @swagger.doc({
        'responses': {
            '200': {
                'description': 'Currently displayed asset in viewer',
                'schema': AssetModel
            }
        }
    })
    def get(self):
        collector = ZmqCollector.get_instance()

        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer('current_asset_id')

        collector_result = collector.recv_json(2000)
        current_asset_id = collector_result.get('current_asset_id')

        if not current_asset_id:
            return []

        with db.conn(settings['database']) as conn:
            return assets_helper.read(conn, current_asset_id)
