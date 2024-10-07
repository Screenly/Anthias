import json

from flask import request
from flask_restful_swagger_2 import Resource, swagger
from os import remove
from werkzeug.wrappers import Request

from api.helpers import (
    AssetModel,
    AssetPropertiesModel,
    AssetRequestModel,
    api_response,
    prepare_asset_v1_2,
    update_asset,
)
from lib import db, assets_helper
from lib.auth import authorized
from lib.utils import url_fails
from settings import settings


class AssetsV1_2(Resource):
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
            return assets_helper.read(conn)

    @api_response
    @swagger.doc({
        'parameters': [
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetRequestModel,
                'required': True
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
        request_environ = Request(request.environ)
        asset = prepare_asset_v1_2(request_environ, unique_name=True)
        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            ids_of_active_assets = [
                x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.create(conn, asset)

            if asset['is_active']:
                ids_of_active_assets.insert(
                    asset['play_order'], asset['asset_id'])
            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset['asset_id']), 201


class AssetV1_2(Resource):
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
                'description': 'ID of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'properties',
                'description': 'Properties of an asset',
                'schema': AssetPropertiesModel,
                'required': True
            }
        ],
        'responses': {
            '200': {
                'description': 'Asset updated',
                'schema': AssetModel
            }
        }
    })
    def patch(self, asset_id):
        data = json.loads(request.data)
        with db.conn(settings['database']) as conn:

            asset = assets_helper.read(conn, asset_id)
            if not asset:
                raise Exception('Asset not found.')
            update_asset(asset, data)

            assets = assets_helper.read(conn)
            ids_of_active_assets = [
                x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.update(conn, asset_id, asset)

            try:
                ids_of_active_assets.remove(asset['asset_id'])
            except ValueError:
                pass
            if asset['is_active']:
                ids_of_active_assets.insert(
                    asset['play_order'], asset['asset_id'])

            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetRequestModel,
                'required': True
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
        asset = prepare_asset_v1_2(request, asset_id)
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            ids_of_active_assets = [
                x['asset_id'] for x in assets if x['is_active']]

            asset = assets_helper.update(conn, asset_id, asset)

            try:
                ids_of_active_assets.remove(asset['asset_id'])
            except ValueError:
                pass
            if asset['is_active']:
                ids_of_active_assets.insert(
                    asset['play_order'], asset['asset_id'])

            assets_helper.save_ordering(conn, ids_of_active_assets)
            return assets_helper.read(conn, asset_id)

    @swagger.doc({
        'parameters': [
            {
                'name': 'asset_id',
                'type': 'string',
                'in': 'path',
                'description': 'id of an asset',
                'required': True

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
