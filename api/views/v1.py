import pydbus
import re
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers import AssetSerializer
from api.helpers import prepare_asset
from base64 import b64encode
from hurry.filesize import size
from lib import (
    assets_helper,
    backup_helper,
    db,
    diagnostics
)
from lib.github import is_up_to_date
from lib.utils import (
    connect_to_redis,
    generate_perfect_paper_password,
    get_active_connections,
    remove_connection,
    url_fails
)
from mimetypes import guess_type, guess_extension
from os import getenv, path, remove, statvfs
from server import celery, reboot_screenly, shutdown_screenly
from settings import settings, ZmqCollector, ZmqPublisher


r = connect_to_redis()


# @TODO: Use the following decorators: api_response, authorized, swagger
class AssetViewV1(APIView):
    serializer_class = AssetSerializer

    def get(self, request, asset_id, format=None):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)

        if isinstance(asset, list) and not asset:
            return Response(
                {'message': 'Asset not found.'},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = self.serializer_class(data=asset)

        if serializer.is_valid():
            return Response(serializer.data)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    def put(self, request, asset_id, format=None):
        with db.conn(settings['database']) as conn:
            result = assets_helper.update(conn, asset_id, prepare_asset(request))

            return Response(result, status=status.HTTP_200_OK)

    def delete(self, request, asset_id, format=None):
        with db.conn(settings['database']) as conn:
            asset = assets_helper.read(conn, asset_id)
            try:
                if asset['uri'].startswith(settings['assetdir']):
                    remove(asset['uri'])
            except OSError:
                pass
            assets_helper.delete(conn, asset_id)
            return Response(status=status.HTTP_204_NO_CONTENT)


# @TODO: Use the following decorators: api_response, authorized, swagger
class AssetContentView(APIView):
    def get(self, request, asset_id, format=None):
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

        return Response(result)


# @TODO: Use the following decorators: authorized, swagger
class AssetListViewV1(APIView):
    serializer_class = AssetSerializer

    def get(self, request, format=None):
        with db.conn(settings['database']) as conn:
            data = assets_helper.read(conn)

        serializer = self.serializer_class(data=data, many=True)

        if serializer.is_valid():
            return Response(serializer.data)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    def post(self, request, format=None):
        asset = prepare_asset(request)

        if url_fails(asset['uri']):
            return Response(
                {'message': 'Could not retrieve file. Check the asset URL.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        with db.conn(settings['database']) as conn:
            result = assets_helper.create(conn, asset)
            return Response(result, status=status.HTTP_201_CREATED)


# @TODO: Use the following decorators: api_response, authorized, swagger
class FileAssetView(APIView):
    def post(self, request):
        file_upload = request.data.get('file_upload')
        filename = file_upload.name
        file_type = guess_type(filename)[0]

        if not file_type:
            raise Exception("Invalid file type.")

        if file_type.split('/')[0] not in ['image', 'video']:
            raise Exception("Invalid file type.")

        file_path = path.join(settings['assetdir'], uuid.uuid5(uuid.NAMESPACE_URL, filename).hex) + ".tmp"

        if 'Content-Range' in request.headers:
            range_str = request.headers['Content-Range']
            start_bytes = int(range_str.split(' ')[1].split('-')[0])
            with open(file_path, 'ab') as f:
                f.seek(start_bytes)
                f.write(file_upload.read())
        else:
            with open(file_path, 'wb') as f:
                f.write(file_upload.read())

        return Response({'uri': file_path, 'ext': guess_extension(file_type)})


# @TODO: Use the following decorators: api_response, authorized, swagger
class PlaylistOrderView(APIView):
    def post(self, request):
        with db.conn(settings['database']) as conn:
            assets_helper.save_ordering(conn, request.data.get('ids', '').split(','))

        return Response(status=status.HTTP_204_NO_CONTENT)


# @TODO: Use the following decorators: api_response, authorized, swagger
class BackupView(APIView):
    def post(self, request):
        filename = backup_helper.create_backup(name=settings['player_name'])
        return Response(filename, status=status.HTTP_201_CREATED)


# @TODO: Use the following decorators: api_response, authorized, swagger
class RecoverView(APIView):
    def post(self, request):
        publisher = ZmqPublisher.get_instance()
        file_upload = (request.files['backup_upload'])
        filename = file_upload.filename

        if guess_type(filename)[0] != 'application/x-tar':
            raise Exception("Incorrect file extension.")
        try:
            publisher.send_to_viewer('stop')
            location = path.join("static", filename)
            file_upload.save(location)
            backup_helper.recover(location)
            return Response("Recovery successful.")
        finally:
            publisher.send_to_viewer('play')


# @TODO: Use the following decorators: api_response, authorized, swagger
class AssetsControlView(APIView):
    def get(self, request, command):
        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer(command)
        return Response("Asset switched")


# @TODO: Use the following decorators: api_response, authorized, swagger
class InfoView(APIView):
    def get(self, request):
        viewlog = "Not yet implemented"

        # Calculate disk space
        slash = statvfs("/")
        free_space = size(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return Response({
            'viewlog': viewlog,
            'loadavg': diagnostics.get_load_avg()['15 min'],
            'free_space': free_space,
            'display_info': diagnostics.get_monitor_status(),
            'display_power': display_power,
            'up_to_date': is_up_to_date()
        })


# @TODO: Use the following decorators: api_response, authorized, swagger
class ResetWifiConfigView(APIView):
    def get(self, request):
        home = getenv('HOME')
        file_path = path.join(home, '.screenly/initialized')

        if path.isfile(file_path):
            remove(file_path)

        bus = pydbus.SystemBus()

        pattern_include = re.compile("wlan*")
        pattern_exclude = re.compile("ScreenlyOSE-*")

        wireless_connections = get_active_connections(bus)

        if wireless_connections is not None:
            device_uuid = None

            wireless_connections = [c for c in [c for c in wireless_connections if pattern_include.search(str(c['Devices']))] if not pattern_exclude.search(str(c['Id']))]

            if len(wireless_connections) > 0:
                device_uuid = wireless_connections[0].get('Uuid')

            if not device_uuid:
                raise Exception('The device has no active connection.')

            remove_connection(bus, device_uuid)

        return Response(status=status.HTTP_204_NO_CONTENT)


# @TODO: Use the following decorators: api_response, authorized, swagger
class GenerateUsbAssetsKeyView(APIView):
    def get(self, request):
        settings['usb_assets_key'] = generate_perfect_paper_password(20, False)
        settings.save()

        return Response(settings['usb_assets_key'])


# @TODO: Use the following decorators: api_response, authorized, swagger
class UpgradeScreenlyView(APIView):
    def post(self, request):
        for task in celery.control.inspect(timeout=2.0).active().get('worker@screenly'):
            if task.get('type') == 'server.upgrade_screenly':
                return Response({'id': task.get('id')})
        branch = request.form.get('branch')
        manage_network = request.form.get('manage_network')
        system_upgrade = request.form.get('system_upgrade')
        task = upgrade_screenly.apply_async(args=(branch, manage_network, system_upgrade))
        return Response({'id': task.id})


# @TODO: Use the following decorators: api_response, authorized, swagger
class RebootScreenlyView(APIView):
    def post(self, request):
        reboot_screenly.apply_async()
        return Response(status=status.HTTP_200_OK)

# @TODO: Use the following decorators: api_response, authorized, swagger
class ShutdownScreenlyView(APIView):
    def post(self, request):
        shutdown_screenly.apply_async()
        return Response(status=status.HTTP_200_OK)


# @TODO: Use the following decorators: api_response, authorized, swagger
class ViewerCurrentAssetView(APIView):
    def get(self):
        collector = ZmqCollector.get_instance()

        publisher = ZmqPublisher.get_instance()
        publisher.send_to_viewer('current_asset_id')

        collector_result = collector.recv_json(2000)
        current_asset_id = collector_result.get('current_asset_id')

        if not current_asset_id:
            return Response([])

        with db.conn(settings['database']) as conn:
            return Response(assets_helper.read(conn, current_asset_id))
