import logging
import re
import tarfile
import uuid
from base64 import b64encode
from contextlib import suppress
from inspect import cleandoc
from mimetypes import guess_extension, guess_type
from os import path, remove, statvfs
from typing import Any

from django.shortcuts import get_object_or_404
from django.template.defaultfilters import filesizeformat
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_server.app.helpers import delete_asset_with_file
from anthias_server.app.models import Asset
from anthias_server.api.helpers import save_active_assets_ordering
from anthias_server.api.serializers.mixins import (
    BackupViewSerializerMixin,
    DisplayPowerViewSerializerMixin,
    PlaylistOrderSerializerMixin,
    RebootViewSerializerMixin,
    ShutdownViewSerializerMixin,
)
from anthias_server.celery_tasks import reboot_anthias, shutdown_anthias
from anthias_server.lib import backup_helper, diagnostics
from anthias_server.lib.auth import authorized
from anthias_server.lib.github import is_up_to_date
from anthias_common.utils import (
    DISK_FULL_ERROR,
    connect_to_redis,
    is_disk_full,
)
from anthias_server.settings import ViewerPublisher, settings

logger = logging.getLogger(__name__)

r = connect_to_redis()


class DeleteAssetViewMixin:
    @extend_schema(summary='Delete asset')
    @authorized
    def delete(self, request: Request, asset_id: str) -> Response:
        asset = get_object_or_404(Asset, asset_id=asset_id)
        delete_asset_with_file(asset)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BackupViewMixin(APIView):
    @extend_schema(
        summary='Create backup',
        description=cleandoc("""
        Create a backup of the current Anthias instance, which
        includes the following:
        * current settings
        * image and video assets
        * asset metadata (e.g. name, duration, play order, status),
          which is stored in a SQLite database
        """),
        request=BackupViewSerializerMixin,
        responses={
            201: {
                'type': 'string',
                'example': 'anthias-backup-2021-09-16T15-00-00.tar.gz',
                'description': 'Backup file name',
            }
        },
    )
    @authorized
    def post(self, request: Request) -> Response:
        filename = backup_helper.create_backup(name=settings['player_name'])
        return Response(filename, status=status.HTTP_201_CREATED)


class RecoverViewMixin(APIView):
    @extend_schema(
        summary='Recover from backup',
        description=cleandoc("""
        Recover data from a backup file. The backup file must be
        a `.tar.gz` file.
        """),
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'backup_upload': {'type': 'string', 'format': 'binary'}
                },
            }
        },
        responses={
            200: {
                'type': 'string',
                'example': 'Recovery successful.',
            }
        },
    )
    @authorized
    def post(self, request: Request) -> Response:
        publisher = ViewerPublisher.get_instance()
        file_upload = request.data.get('backup_upload')
        if file_upload is None:
            raise ValidationError(
                {'backup_upload': 'No backup file uploaded.'}
            )
        filename = file_upload.name

        if guess_type(filename)[0] != 'application/x-tar':
            raise ValidationError(
                {'backup_upload': 'Incorrect file extension.'}
            )
        # Don't trust the client-supplied filename — generate a
        # server-side name to avoid path traversal via crafted names
        # (e.g. '../etc/passwd', absolute paths).
        location = path.join('static', f'{uuid.uuid4().hex}.tar.gz')
        try:
            publisher.send_to_viewer('stop')

            with open(location, 'wb') as f:
                f.write(file_upload.read())

            try:
                backup_helper.recover(location)
            except (
                backup_helper.BackupRecoverError,
                tarfile.TarError,
            ):
                logger.exception('Backup recovery failed')
                raise ValidationError(
                    {'backup_upload': 'Invalid backup archive.'}
                )

            return Response('Recovery successful.')
        finally:
            # recover() removes `location` on success; clean up here for
            # every failure path so partial uploads / rejected archives
            # don't accumulate under static/.
            if path.isfile(location):
                try:
                    remove(location)
                except OSError:
                    logger.exception(
                        'Failed to remove leftover backup upload at %s',
                        location,
                    )
            publisher.send_to_viewer('play')


class RebootViewMixin(APIView):
    serializer_class = RebootViewSerializerMixin

    # Empty body on success; declare it so drf-spectacular doesn't
    # invent a default schema from the (empty) request serializer.
    # Matches the pattern DisplayPowerViewMixin uses below.
    @extend_schema(summary='Reboot system', responses={200: None})
    @authorized
    def post(self, request: Request) -> Response:
        reboot_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class ShutdownViewMixin(APIView):
    serializer_class = ShutdownViewSerializerMixin

    @extend_schema(summary='Shut down system', responses={200: None})
    @authorized
    def post(self, request: Request) -> Response:
        shutdown_anthias.apply_async()
        return Response(status=status.HTTP_200_OK)


class DisplayPowerViewMixin(APIView):
    serializer_class = DisplayPowerViewSerializerMixin

    @extend_schema(
        summary='Set display power state (experimental, HDMI-CEC)',
        parameters=[
            OpenApiParameter(
                name='state',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.STR,
                enum=['on', 'off'],
                description=(
                    'Desired display power state. Only valid on '
                    'CEC-capable hardware.'
                ),
            ),
        ],
        # Every status returns the same `{message: ...}` shape. Mapping
        # each code to the serializer keeps drf-spectacular's generated
        # OpenAPI document accurate so clients know what to parse.
        responses={
            200: DisplayPowerViewSerializerMixin,
            400: DisplayPowerViewSerializerMixin,
            502: DisplayPowerViewSerializerMixin,
            503: DisplayPowerViewSerializerMixin,
        },
    )
    @authorized
    def post(self, request: Request, state: str) -> Response:
        if state not in ('on', 'off'):
            return Response(
                {'message': 'Invalid display state.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # No /dev/cec0 or /dev/vchiq — fail fast with 503 rather than
        # spawning a 10 s libcec subprocess that's guaranteed to error.
        if not diagnostics.cec_available():
            return Response(
                {'message': 'No HDMI-CEC adapter detected on this device.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        ok, msg = diagnostics.set_display_power(on=(state == 'on'))
        if ok:
            return Response({'message': msg}, status=status.HTTP_200_OK)
        # 502: upstream CEC adapter / TV refused or didn't respond.
        return Response({'message': msg}, status=status.HTTP_502_BAD_GATEWAY)


class FileAssetViewMixin(APIView):
    @extend_schema(
        summary='Upload file asset',
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file_upload': {'type': 'string', 'format': 'binary'}
                },
            }
        },
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'uri': {'type': 'string'},
                    'ext': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def post(self, request: Request) -> Response:
        # ``request.data`` triggers the (lazy) multipart parse, which
        # spools the body to a temp file — on a full disk that write
        # is where ENOSPC actually surfaces (Sentry ANTHIAS-3K).
        try:
            file_upload = request.data.get('file_upload')
        except OSError as exc:
            if not is_disk_full(exc):
                raise
            return Response(
                {'detail': DISK_FULL_ERROR},
                status=status.HTTP_507_INSUFFICIENT_STORAGE,
            )
        if file_upload is None:
            raise ValidationError({'file_upload': 'No file uploaded.'})
        filename = file_upload.name
        file_type = guess_type(filename)[0]

        if not file_type or file_type.split('/')[0] not in ['image', 'video']:
            raise ValidationError(
                {'file_upload': 'Invalid file type. Expected image or video.'}
            )

        file_path = (
            path.join(
                settings['assetdir'],
                uuid.uuid5(uuid.NAMESPACE_URL, filename).hex,
            )
            + '.tmp'
        )

        has_range = 'Content-Range' in request.headers
        start_bytes = 0
        end_bytes = 0
        total_bytes = 0
        data = file_upload.read()
        if has_range:
            # ``Content-Range`` is client-controlled; parse it strictly
            # and 400 on anything malformed rather than letting a bad
            # header raise ValueError/IndexError and surface as a 500.
            # A known numeric total is required (``*`` is rejected): the
            # end-of-upload truncation below relies on it to drop stale
            # trailing bytes, so an unknown total would reopen the
            # corruption window it exists to close. Our uploader always
            # knows the file size.
            match = re.fullmatch(
                r'bytes (\d+)-(\d+)/(\d+)',
                request.headers['Content-Range'].strip(),
            )
            if match is None:
                raise ValidationError(
                    {'Content-Range': 'Malformed Content-Range header.'}
                )
            start_bytes = int(match.group(1))
            end_bytes = int(match.group(2))
            total_bytes = int(match.group(3))
            # Reject inconsistent numeric semantics: end before start, an
            # end at/after the (0-indexed) total, or a chunk body whose
            # length doesn't match the declared range. Any of these would
            # otherwise silently write a misaligned/short chunk and
            # corrupt the reassembled asset.
            if end_bytes < start_bytes or end_bytes >= total_bytes:
                raise ValidationError(
                    {'Content-Range': 'Invalid Content-Range bounds.'}
                )
            if len(data) != end_bytes - start_bytes + 1:
                raise ValidationError(
                    {
                        'Content-Range': (
                            'Chunk length does not match the declared range.'
                        )
                    }
                )

        try:
            if has_range:
                # ``r+b`` (not ``ab``): append mode pins every write to
                # EOF and silently ignores ``seek()``, so an out-of-
                # order chunk would land at the wrong offset and corrupt
                # the ``.tmp``. Open the existing file for in-place
                # random-access writes; if it doesn't exist yet, ``wb``
                # creates it.
                mode = 'r+b' if path.isfile(file_path) else 'wb'
                with open(file_path, mode) as f:
                    f.seek(start_bytes)
                    f.write(data)
                    # On the final chunk, truncate to the declared total
                    # so stale trailing bytes from a previous, longer
                    # upload to this deterministic path can't survive
                    # into the reassembled asset. Order-independent: the
                    # file ends up exactly ``total_bytes`` long whenever
                    # the last byte is written, regardless of chunk
                    # arrival order.
                    if end_bytes + 1 == total_bytes:
                        f.truncate(total_bytes)
            else:
                with open(file_path, 'wb') as f:
                    f.write(data)
        except OSError as exc:
            if not is_disk_full(exc):
                raise
            # Don't leave a truncated .tmp squatting on the last free
            # bytes of an already-full disk.
            with suppress(OSError):
                remove(file_path)
            return Response(
                {'detail': DISK_FULL_ERROR},
                status=status.HTTP_507_INSUFFICIENT_STORAGE,
            )

        return Response({'uri': file_path, 'ext': guess_extension(file_type)})


class AssetContentViewMixin(APIView):
    @extend_schema(
        summary='Get asset content',
        description=cleandoc("""
        The content of the asset.
        `type` can either be `file` or `url`.

        In case of a file, the fields `mimetype`, `filename`, and `content`
        will be present. In case of a URL, the field `url` will be present.
        """),
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string'},
                    'url': {'type': 'string'},
                    'filename': {'type': 'string'},
                    'mimetype': {'type': 'string'},
                    'content': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def get(
        self,
        request: Request,
        asset_id: str,
        format: str | None = None,
    ) -> Response:
        asset = get_object_or_404(Asset, asset_id=asset_id)
        if asset.uri is None:
            raise NotFound('Asset has no content URI.')

        result: dict[str, Any]
        if path.isfile(asset.uri):
            filename = asset.name or ''

            with open(asset.uri, 'rb') as f:
                content = f.read()

            mimetype = guess_type(filename)[0]
            if not mimetype:
                mimetype = 'application/octet-stream'

            result = {
                'type': 'file',
                'filename': filename,
                'content': b64encode(content).decode(),
                'mimetype': mimetype,
            }
        else:
            result = {'type': 'url', 'url': asset.uri}

        return Response(result)


class PlaylistOrderViewMixin(APIView):
    @extend_schema(
        summary='Update playlist order',
        request=PlaylistOrderSerializerMixin,
        responses={204: None},
    )
    @authorized
    def post(self, request: Request) -> Response:
        asset_ids = request.data.get('ids', '').split(',')
        save_active_assets_ordering(asset_ids)

        return Response(status=status.HTTP_204_NO_CONTENT)


class AssetsControlViewMixin(APIView):
    @extend_schema(
        summary='Control asset playback',
        description=cleandoc("""
        Use any of the following commands to control asset playback:
        * `next` - Show the next asset
        * `previous` - Show the previous asset
        * `asset&{asset_id}` - Show the asset with the specified `asset_id`
        """),
        responses={
            200: {
                'type': 'string',
                'example': 'Asset switched',
            }
        },
        parameters=[
            OpenApiParameter(
                name='command',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.STR,
                enum=['next', 'previous', 'asset&{asset_id}'],
            )
        ],
    )
    @authorized
    def get(self, request: Request, command: str) -> Response:
        publisher = ViewerPublisher.get_instance()
        publisher.send_to_viewer(command)
        return Response('Asset switched')


class InfoViewMixin(APIView):
    @extend_schema(
        summary='Get system information',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'viewlog': {'type': 'string'},
                    'loadavg': {'type': 'number'},
                    'free_space': {'type': 'string'},
                    'display_power': {'type': 'string'},
                    'up_to_date': {'type': 'boolean'},
                },
                'example': {
                    'viewlog': 'Not yet implemented',
                    'loadavg': 0.1,
                    # Shape matches ``django.template.defaultfilters.filesizeformat``:
                    # number with one decimal, non-breaking space ( ),
                    # full unit label (KB / MB / GB / TB). Old hurry.filesize
                    # output ("10G") was removed in the 2026.05.1 release.
                    'free_space': '10.0 GB',
                    'display_power': 'on',
                    'up_to_date': True,
                },
            }
        },
    )
    @authorized
    def get(self, request: Request) -> Response:
        viewlog = 'Not yet implemented'

        # Calculate disk space
        slash = statvfs('/')
        free_space = filesizeformat(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return Response(
            {
                'viewlog': viewlog,
                'loadavg': diagnostics.get_load_avg()['15 min'],
                'free_space': free_space,
                'display_power': display_power,
                'up_to_date': is_up_to_date(),
            }
        )
