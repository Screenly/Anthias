import logging
import os
import re
import tarfile
import uuid
from base64 import b64encode
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from inspect import cleandoc
from mimetypes import guess_extension, guess_type
from os import path, remove, statvfs
from typing import BinaryIO

from asgiref.sync import sync_to_async
from django.http import HttpResponseBase, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import filesizeformat
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_common.utils import (
    DISK_FULL_ERROR,
    connect_to_redis,
    is_disk_full,
)
from anthias_server.api.helpers import save_active_assets_ordering
from anthias_server.api.serializers.mixins import (
    BackupViewSerializerMixin,
    DisplayPowerViewSerializerMixin,
    PlaylistOrderSerializerMixin,
    RebootViewSerializerMixin,
    ShutdownViewSerializerMixin,
)
from anthias_server.app.helpers import delete_asset_with_file
from anthias_server.app.models import Asset
from anthias_server.celery_tasks import reboot_anthias, shutdown_anthias
from anthias_server.lib import backup_helper, diagnostics
from anthias_server.lib.auth import authorized
from anthias_server.lib.github import is_up_to_date
from anthias_server.settings import ViewerPublisher, settings

logger = logging.getLogger(__name__)

r = connect_to_redis()

# A resumable upload's temp file is named ``<upload_id>.tmp``. The id is
# echoed back by the client on every chunk, so it lands in a filesystem
# path — require the 32-lowercase-hex-char shape of the ids we mint
# (``uuid4().hex``) and reject anything else. This is a path-traversal
# guard, not a UUID-version check: 32 hex chars simply can't contain a
# ``/`` or ``..`` to escape the asset dir.
UPLOAD_ID_RE = re.compile(r'[0-9a-f]{32}')

# How much of an asset ``AssetContentViewMixin`` base64-encodes per
# step. Must stay a multiple of 3: base64 maps 3 input bytes to 4
# output characters, so only on 3-byte boundaries does encoding the
# pieces and concatenating give the same string as encoding the whole
# file — anywhere else each piece picks up its own ``=`` padding and
# the parts no longer splice into valid base64.
B64_STREAM_CHUNK_SIZE = 3 * 1024 * 1024


def build_file_content_response(
    handle: BinaryIO,
    size: int,
    filename: str,
    mimetype: str,
    accepted_media_type: str | None = None,
) -> StreamingHttpResponse:
    """Stream ``{"type": "file", ..., "content": "<base64>"}``.

    Byte-for-byte the same JSON the view used to hand DRF as a dict,
    but produced in ``B64_STREAM_CHUNK_SIZE`` steps so the server's
    memory stays flat instead of scaling with the asset. The old path
    held the file, its base64 copy and the rendered response at once —
    measured ~4x the file size as RSS on anthias-server for JSON, and
    ~14x when a browser's ``Accept: text/html`` selected the browsable
    renderer, which is more than enough to OOM a board under
    ``LOW_RAM_THRESHOLD_KB`` (issue #3345).

    The envelope is assembled by rendering the real payload through
    DRF's own ``JSONRenderer`` with a one-shot random sentinel standing
    in for ``content``, then splitting on it. Hand-writing the JSON
    would fork the escaping rules for ``filename`` (non-ASCII asset
    names, quotes) away from whatever the renderer is configured to do;
    this way only the base64 — which is ASCII and needs no escaping —
    bypasses it. The sentinel is a fresh uuid4 hex rather than a fixed
    marker so no asset name can collide with it, and being plain
    alphanumerics it survives rendering unescaped.

    Streaming means DRF content negotiation no longer applies to file
    assets: the browsable API cannot render a multi-gigabyte base64
    blob into an HTML page without reintroducing the bug it exists to
    fix, so this always responds ``application/json``. URL assets keep
    the negotiated ``Response``.
    """
    sentinel = uuid.uuid4().hex.encode()
    # ``accepted_media_type`` is forwarded so an ``application/json;
    # indent=4`` request still pretty-prints. Without it this branch
    # would quietly ignore the parameter that the URL branch — still a
    # DRF ``Response`` — keeps honouring, and one endpoint would format
    # its two shapes differently.
    envelope = JSONRenderer().render(
        {
            'type': 'file',
            'filename': filename,
            'content': sentinel.decode(),
            'mimetype': mimetype,
        },
        accepted_media_type=accepted_media_type,
    )
    head, found, tail = envelope.partition(b'"' + sentinel + b'"')
    if not found:
        raise RuntimeError('Unexpected JSON envelope for asset content.')

    def pieces() -> Iterator[bytes]:
        with handle:
            yield head + b'"'
            remaining = size
            # 0-2 bytes left over when a read doesn't land on a 3-byte
            # boundary, carried into the next encode so the pieces
            # still splice. Reads off a regular file are exact, so this
            # only ever fills on the truncation path below.
            pending = b''
            while remaining > 0:
                chunk = handle.read(min(B64_STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    # Truncated under us. Make the shortfall up with
                    # NULs rather than stopping short: we have already
                    # promised a Content-Length, and a body that never
                    # reaches it leaves the client waiting on bytes
                    # that will not come.
                    chunk = b'\x00' * min(B64_STREAM_CHUNK_SIZE, remaining)
                remaining -= len(chunk)
                chunk = pending + chunk
                # Hold back a partial group unless this is the last
                # chunk, which takes the trailing ``=`` padding.
                cut = (
                    len(chunk)
                    if remaining == 0
                    else len(chunk) - len(chunk) % 3
                )
                pending = chunk[cut:]
                yield b64encode(chunk[:cut])
            yield b'"' + tail

    async def apieces() -> AsyncIterator[bytes]:
        """``pieces()`` as an async iterator, reads off the event loop.

        Handing ``StreamingHttpResponse`` the synchronous generator
        directly looks like it works and silently undoes the whole
        fix: under ASGI — which is how anthias-server runs — Django's
        ``StreamingHttpResponse.__aiter__`` funnels a sync iterator
        through ``await sync_to_async(list)(...)``, draining it into a
        list in full before the first byte reaches the client. Memory
        then tracks the response again, just one layer further out
        (measured +405 MB RSS for a 300 MB asset, versus +15 MB here).

        Each ``next()`` runs in a worker thread rather than inline:
        one step reads ``B64_STREAM_CHUNK_SIZE`` off what may be an SD
        card, and blocking the event loop for that long stalls every
        other request the device is serving.
        """
        iterator = pieces()
        step = sync_to_async(_next_piece, thread_sensitive=False)
        while True:
            piece = await step(iterator)
            if piece is None:
                return
            yield piece

    # Wrapped rather than passed bare so Django registers a teardown
    # closer for the handle — see ``_ClosingAsyncBody``.
    response = StreamingHttpResponse(
        _ClosingAsyncBody(apieces(), handle),
        content_type='application/json',
    )
    # Known up front, so clients keep the progress bar the buffered
    # response gave them.
    response.headers['Content-Length'] = str(
        len(head) + 1 + _b64_len(size) + 1 + len(tail)
    )
    return response


class _ClosingAsyncBody:
    """An async response body that owns — and can close — its file.

    ``StreamingHttpResponse`` registers a teardown closer only when the
    object it is handed exposes ``close``. A bare async generator
    exposes ``aclose`` instead, so nothing is registered and the asset's
    fd survives ``response.close()`` — which, on a download the client
    aborts, is the only cleanup Django runs. The fd and its inode would
    then stay pinned until GC finalised the abandoned generator, so a
    deleted asset would not even give its disk space back.

    Wrapping the generator in an object with a real ``close`` hands the
    handle to Django's own resource management, rather than reaching
    into ``response._resource_closers`` behind its back.
    """

    def __init__(self, body: AsyncIterator[bytes], handle: BinaryIO) -> None:
        self._body = body
        self._handle = handle

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._body

    def close(self) -> None:
        self._handle.close()


def _b64_len(size: int) -> int:
    """Length of ``b64encode`` output for ``size`` input bytes."""
    return 4 * ((size + 2) // 3)


def _next_piece(iterator: Iterator[bytes]) -> bytes | None:
    """One step of a body generator, or ``None`` when it is spent.

    A named function rather than ``next`` itself so the value handed
    to ``sync_to_async`` has a single, checkable signature.
    """
    return next(iterator, None)


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
        # DRF types request.data as dict | list; a JSON list body would
        # make .get() raise (500). Treat any non-dict body as "no
        # upload" so it falls through to the 400 below.
        data = request.data
        file_upload = (
            data.get('backup_upload') if isinstance(data, dict) else None
        )
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

            # Stream the upload to disk in chunks — ``file_upload.read()``
            # pulls the whole archive into RAM, and a backup is every
            # image + video asset on the device, so a multi-GB restore
            # OOM-kills the worker on a 1 GB Pi. The HTML recover view
            # (settings_recover) already streams; this brings the API
            # path in line.
            with open(location, 'wb') as f:
                f.writelines(file_upload.chunks())

            try:
                backup_helper.recover(location)
            except backup_helper.IncompatibleBackupError as exc:
                # Version mismatch is operator-actionable ("upgrade
                # first"); surface its specific message.
                logger.warning('Backup restore rejected: %s', exc)
                raise ValidationError({'backup_upload': str(exc)})
            except (
                backup_helper.BackupRecoverError,
                tarfile.TarError,
            ) as exc:
                # Operator uploaded something that isn't a valid Anthias
                # backup (wrong file, truncated, not gzip). That's input
                # validation, not a bug — the 400 below already tells
                # them. Log at warning so it doesn't reach the Sentry
                # logging integration as an error (Sentry ANTHIAS-3W).
                logger.warning('Backup recovery failed: %s', exc)
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
        # No CEC adapter on this device — fail fast with 503 rather
        # than attempting a transmit that cannot succeed.
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
        parameters=[
            OpenApiParameter(
                name='X-Upload-Id',
                location=OpenApiParameter.HEADER,
                type=OpenApiTypes.STR,
                required=False,
                description=(
                    'Opaque upload session id returned as ``upload_id`` on '
                    'the first chunk of a resumable (Content-Range) upload. '
                    'Echo it on every subsequent chunk so they reassemble '
                    'into the same file. Omit it to start a new upload.'
                ),
            ),
        ],
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
                    'upload_id': {'type': 'string'},
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
            data = request.data
            file_upload = (
                data.get('file_upload') if isinstance(data, dict) else None
            )
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

        has_range = 'Content-Range' in request.headers

        # Stage the upload at a per-request temp path. The id is random
        # (uuid4) and never derived from the filename: a filename-derived
        # path is shared by every upload of that name, so two concurrent
        # same-name uploads would interleave into one corrupt file and a
        # stale ``.tmp`` from an earlier interrupted attempt would bleed
        # into a later one (issue #3135). A resumable (Content-Range)
        # upload gets one isolated file per session: the client echoes the
        # ``upload_id`` we return on the first chunk back via ``X-Upload-Id``
        # so every chunk lands in the same file.
        #
        # ``X-Upload-Id`` is only honoured alongside ``Content-Range``: a
        # single-shot upload takes the ``open('wb')`` path below, so an
        # echoed id there would let one request truncate another session's
        # in-progress ``.tmp``. Without a range we always mint a fresh id.
        upload_id = request.headers.get('X-Upload-Id') if has_range else None
        if upload_id is None:
            upload_id = uuid.uuid4().hex
        else:
            # Normalise case (the id we mint is lowercase hex) before the
            # traversal guard so an upper-cased echo doesn't 400.
            upload_id = upload_id.strip().lower()
            if not UPLOAD_ID_RE.fullmatch(upload_id):
                raise ValidationError({'X-Upload-Id': 'Malformed upload id.'})

        file_path = path.join(settings['assetdir'], upload_id) + '.tmp'

        start_bytes = 0
        end_bytes = 0
        total_bytes = 0
        # Never materialise the body: the upload is copied to disk with
        # ``chunks()`` below, and the length checks read the size the
        # multipart parser already recorded. A single-shot ``.read()``
        # here cost one full copy of the file in RAM (a 300 MB upload
        # measured +302 MB RSS on anthias-server), which is enough to
        # OOM every board under ``LOW_RAM_THRESHOLD_KB`` (issue #3345).
        upload_size = file_upload.size
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
            if upload_size != end_bytes - start_bytes + 1:
                raise ValidationError(
                    {
                        'Content-Range': (
                            'Chunk length does not match the declared range.'
                        )
                    }
                )

        try:
            if has_range:
                # ``os.open`` with ``O_CREAT`` and no ``O_TRUNC``: create
                # the file on the first chunk, otherwise open the
                # in-progress upload for random-access writes without
                # discarding the bytes already written. This closes the
                # ``isfile()``-then-``open('wb')`` race where two chunks
                # arriving together could both see "no file" and clobber
                # each other. ``r+b`` (not append) so ``seek()`` decides
                # the offset and an out-of-order chunk lands where the
                # range says. ``0o666`` (masked by the process umask) and
                # ``O_CLOEXEC`` match what the builtin ``open()`` on the
                # non-range path below does, so permissions stay
                # umask-controlled and the fd doesn't leak into forked
                # subprocesses.
                fd = os.open(
                    file_path,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
                    0o666,
                )
                with os.fdopen(fd, 'r+b') as f:
                    f.seek(start_bytes)
                    for chunk in file_upload.chunks():
                        f.write(chunk)
                    # On the final chunk, truncate to the declared total
                    # so a resumed session that shrank can't keep trailing
                    # bytes from a longer earlier attempt. Order-
                    # independent: the file ends up exactly ``total_bytes``
                    # long whenever the last byte is written, regardless
                    # of chunk arrival order.
                    if end_bytes + 1 == total_bytes:
                        f.truncate(total_bytes)
            else:
                with open(file_path, 'wb') as f:
                    for chunk in file_upload.chunks():
                        f.write(chunk)
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

        return Response(
            {
                'uri': file_path,
                'ext': guess_extension(file_type),
                'upload_id': upload_id,
            }
        )


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
    ) -> HttpResponseBase:
        asset = get_object_or_404(Asset, asset_id=asset_id)
        if asset.uri is None:
            raise NotFound('Asset has no content URI.')

        if not path.isfile(asset.uri):
            return Response({'type': 'url', 'url': asset.uri})

        filename = asset.name or ''
        mimetype = guess_type(filename)[0] or 'application/octet-stream'

        # Open before building the response, not inside the generator:
        # an asset deleted between the ``isfile`` above and the first
        # read then still surfaces as a clean 404 instead of a 200 whose
        # body dies halfway through, after the status line has shipped.
        #
        # Only ``FileNotFoundError``, deliberately. A broader ``OSError``
        # would dress a failing SD card (EIO/EUCLEAN), a permissions
        # mistake (EACCES) or fd exhaustion (EMFILE) up as "no such
        # asset" — a clean 404 that tells Sentry nothing and that a
        # backup client silently skips over. Those belong in a 500.
        try:
            # SIM115 false positive: the handle *is* managed, by the
            # generator in build_file_content_response, which owns it
            # for the life of the response. A ``with`` here would
            # close it before the first byte is streamed.
            handle = open(asset.uri, 'rb')  # noqa: SIM115
        except FileNotFoundError as exc:
            raise NotFound('Asset content is no longer available.') from exc

        # Size the body from the open handle rather than the path, so
        # the Content-Length we advertise describes exactly the bytes
        # the generator below reads from *this* handle.
        size = os.fstat(handle.fileno()).st_size
        return build_file_content_response(
            handle,
            size,
            filename,
            mimetype,
            accepted_media_type=request.accepted_media_type,
        )


class PlaylistOrderViewMixin(APIView):
    @extend_schema(
        summary='Update playlist order',
        request=PlaylistOrderSerializerMixin,
        responses={204: None},
    )
    @authorized
    def post(self, request: Request) -> Response:
        data = request.data
        if not isinstance(data, dict):
            raise ValidationError(
                {'ids': 'Expected an object body with an "ids" field.'}
            )
        asset_ids = data.get('ids', '').split(',')
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
