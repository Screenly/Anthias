from flask import request
from flask_restful_swagger_2 import Resource, swagger
from os import remove

from api.helpers import (
    AssetModel,
    api_response,
    prepare_asset,
)
from lib import db, assets_helper
from lib.auth import authorized
from lib.utils import url_fails
from settings import settings


class AssetsV1_1(Resource):
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
                'in': 'body',
                'name': 'model',
                'description': 'Adds a asset',
                'schema': AssetModel,
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
        asset = prepare_asset(request, unique_name=True)
        if url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")
        with db.conn(settings['database']) as conn:
            return assets_helper.create(conn, asset), 201


class AssetV1_1(Resource):
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
                'description': 'id of an asset',
                'required': True
            },
            {
                'in': 'body',
                'name': 'model',
                'description': 'Adds an asset',
                'schema': AssetModel,
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
        with db.conn(settings['database']) as conn:
            return assets_helper.update(conn, asset_id, prepare_asset(request))

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
