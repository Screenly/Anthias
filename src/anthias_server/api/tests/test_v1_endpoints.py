"""
Tests for V1 API endpoints.
"""

import base64
import errno
import json
import os
import warnings
from collections.abc import Iterator
from contextlib import suppress
from mimetypes import guess_type
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import UploadedFile
from django.http import StreamingHttpResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from anthias_server.api.tests.test_common import ASSET_CREATION_DATA
from anthias_server.api.views import mixins
from anthias_server.api.views.mixins import B64_STREAM_CHUNK_SIZE
from anthias_server.app.models import Asset
from anthias_server.settings import settings as anthias_settings


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def isolated_asset_dir(tmp_path: Path) -> Iterator[None]:
    """Give the test its own asset dir.

    Named for what it does: it isolates, it does not clean up. Most of
    its users never write into the asset dir at all — the dependency is
    vestigial there, so don't read coverage into it.

    This used to empty ``settings['assetdir']`` on teardown, which is
    one real directory — ~/anthias_assets on a developer's machine —
    shared by every xdist worker. Under ``pytest -n auto`` one worker's
    teardown deleted files another worker was still using, which
    surfaced as rare failures in whichever upload test happened to be
    mid-request. It also emptied a developer's actual asset directory,
    the hazard the backup tests' own fixture calls out.
    """
    asset_dir = tmp_path / 'anthias_assets'
    asset_dir.mkdir()
    with mock.patch.dict(anthias_settings, {'assetdir': str(asset_dir)}):
        yield


def _get_asset_content_url(asset_id: str) -> str:
    return str(reverse('api:asset_content_v1', args=[asset_id]))


@pytest.mark.django_db
def test_asset_content(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    asset = Asset.objects.create(**ASSET_CREATION_DATA)
    asset_id = asset.asset_id

    response = api_client.get(_get_asset_content_url(asset_id))
    data = response.data

    assert response.status_code == status.HTTP_200_OK
    assert data['type'] == 'url'
    assert data['url'] == 'https://anthias.screenly.io'


@pytest.mark.django_db
def test_file_asset(api_client: APIClient, isolated_asset_dir: None) -> None:
    image_path = os.path.join(
        django_settings.BASE_DIR,
        'src/anthias_server/app/static/img/standby.png',
    )

    with open(image_path, 'rb') as file_upload:
        response = api_client.post(
            reverse('api:file_asset_v1'),
            data={'file_upload': file_upload},
        )
    data = response.data

    assert response.status_code == status.HTTP_200_OK
    assert os.path.exists(data['uri'])
    assert data['ext'] == '.png'


@pytest.mark.django_db
def test_file_asset_rejects_list_body(api_client: APIClient) -> None:
    # DRF parses a JSON array body into a list, so request.data.get(...)
    # would raise AttributeError (500). The endpoint must reject a
    # non-dict body with a 400 instead.
    response = api_client.post(
        reverse('api:file_asset_v1'),
        data=['not', 'a', 'dict'],
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_file_asset_disk_full_returns_507(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """ENOSPC while writing the upload must come back as an actionable
    507 with the shared disk-full message, not an unhandled 500
    (Sentry ANTHIAS-3K)."""
    import errno

    image_path = os.path.join(
        django_settings.BASE_DIR,
        'src/anthias_server/app/static/img/standby.png',
    )

    with (
        open(image_path, 'rb') as file_upload,
        mock.patch(
            'anthias_server.api.views.mixins.open',
            side_effect=OSError(errno.ENOSPC, 'No space left on device'),
            create=True,
        ),
    ):
        response = api_client.post(
            reverse('api:file_asset_v1'),
            data={'file_upload': file_upload},
        )

    assert response.status_code == status.HTTP_507_INSUFFICIENT_STORAGE
    assert 'disk is full' in response.data['detail']


@pytest.mark.django_db
def test_file_asset_disk_full_during_parse_returns_507(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """The ANTHIAS-3K stack is ENOSPC during the multipart parse
    (Django spooling the body to a temp file), surfaced when the view
    accesses ``request.data``. Force the parser to raise and assert
    the same 507 + shared message."""
    import errno

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.http.multipartparser import MultiPartParser

    with mock.patch.object(
        MultiPartParser,
        'parse',
        side_effect=OSError(errno.ENOSPC, 'No space left on device'),
    ):
        response = api_client.post(
            reverse('api:file_asset_v1'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.png', b'\x89PNG\r\n', content_type='image/png'
                )
            },
        )

    assert response.status_code == status.HTTP_507_INSUFFICIENT_STORAGE
    assert 'disk is full' in response.data['detail']


@pytest.mark.django_db
def test_file_asset_disk_full_during_write_cleans_up_partial(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """When the disk fills mid-write (open() succeeds, f.write() then
    raises ENOSPC), the handler must remove the partial .tmp and still
    return 507 — not leave a truncated file behind."""
    import errno

    from django.core.files.uploadedfile import SimpleUploadedFile

    write_fails = mock.mock_open()
    write_fails.return_value.write.side_effect = OSError(
        errno.ENOSPC, 'No space left on device'
    )
    with (
        mock.patch(
            'anthias_server.api.views.mixins.open', write_fails, create=True
        ),
        mock.patch('anthias_server.api.views.mixins.remove') as mock_remove,
    ):
        response = api_client.post(
            reverse('api:file_asset_v1'),
            data={
                'file_upload': SimpleUploadedFile(
                    'photo.png', b'\x89PNG\r\n', content_type='image/png'
                )
            },
        )

    assert response.status_code == status.HTTP_507_INSUFFICIENT_STORAGE
    assert 'disk is full' in response.data['detail']
    mock_remove.assert_called_once()


@pytest.mark.django_db
def test_file_asset_chunked_out_of_order_reassembles(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """A resumable (Content-Range) upload must reassemble correctly
    even when chunks arrive out of order. Append mode ignored the
    seek() and pinned every write to EOF, corrupting the .tmp; r+b
    honours the offset. Chunks are tied to one file by the opaque
    ``upload_id`` echoed via ``X-Upload-Id`` (issue #3135), not by the
    filename."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse('api:file_asset_v1')
    # Post the tail chunk first, then the head, to prove the offset —
    # not arrival order — decides where bytes land.
    tail = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'BBBB', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 4-7/8'},
    )
    assert tail.status_code == status.HTTP_200_OK
    upload_id = tail.data['upload_id']
    head = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-3/8', 'X-Upload-Id': upload_id},
    )

    assert head.status_code == status.HTTP_200_OK
    assert head.data['uri'] == tail.data['uri']
    with open(head.data['uri'], 'rb') as f:
        assert f.read() == b'AAAABBBB'


@pytest.mark.django_db
@pytest.mark.parametrize(
    'header',
    [
        'garbage',
        'bytes abc-def/8',
        'bytes 0-3',
        '0-3/8',
        'bytes 0-3/*',
        'bytes 5-3/8',
        'bytes 0-8/8',
    ],
    ids=[
        'non-range',
        'non-numeric',
        'no-total',
        'no-unit',
        'unknown-total',
        'end-before-start',
        'end-at-or-past-total',
    ],
)
def test_file_asset_malformed_content_range_returns_400(
    api_client: APIClient, isolated_asset_dir: None, header: str
) -> None:
    """A client-controlled ``Content-Range`` header must be validated:
    a syntactically malformed value or inconsistent numeric bounds
    returns 400, not a 500 from a split()/int() crash."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = api_client.post(
        reverse('api:file_asset_v1'),
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': header},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_file_asset_content_range_chunk_length_mismatch_returns_400(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """The chunk body length must match the declared range; a mismatch
    (here 4 bytes for a claimed 10-byte range) is a 400, not a silently
    misaligned write."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = api_client.post(
        reverse('api:file_asset_v1'),
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-9/10'},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_file_asset_same_name_uploads_are_isolated(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """Two uploads of the same filename must stage at different temp
    paths so they can't clobber or bleed into each other (issue #3135).
    Each mints its own opaque ``upload_id`` and lands in its own file."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse('api:file_asset_v1')
    first = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
    )
    second = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'BBBBBBBB', content_type='image/png'
            )
        },
    )
    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.data['upload_id'] != second.data['upload_id']
    assert first.data['uri'] != second.data['uri']
    with open(first.data['uri'], 'rb') as f:
        assert f.read() == b'AAAA'
    with open(second.data['uri'], 'rb') as f:
        assert f.read() == b'BBBBBBBB'


@pytest.mark.django_db
def test_file_asset_content_range_truncates_on_shrink(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """Within one upload session (shared ``upload_id``), the final chunk
    truncates to the declared total so a shrunk re-write can't inherit
    trailing bytes from a longer earlier attempt to the same file."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse('api:file_asset_v1')
    # First write: a 10-byte file in one chunk.
    first = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'XXXXXXXXXX', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-9/10'},
    )
    assert first.status_code == status.HTTP_200_OK
    upload_id = first.data['upload_id']
    # Re-write the same session (same upload_id => same .tmp) shorter.
    second = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-3/4', 'X-Upload-Id': upload_id},
    )
    assert second.status_code == status.HTTP_200_OK
    assert second.data['uri'] == first.data['uri']
    with open(second.data['uri'], 'rb') as f:
        assert f.read() == b'AAAA'


@pytest.mark.django_db
def test_file_asset_malformed_upload_id_returns_400(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """``X-Upload-Id`` becomes a filesystem path, so a value that isn't
    the uuid4 hex shape we mint (here a traversal attempt) must 400
    rather than escape the asset dir."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = api_client.post(
        reverse('api:file_asset_v1'),
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={
            'Content-Range': 'bytes 0-3/4',
            'X-Upload-Id': '../../etc/passwd',
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_file_asset_upload_id_ignored_without_content_range(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """A single-shot (no Content-Range) upload takes the ``open('wb')``
    truncating path, so ``X-Upload-Id`` must be ignored there — otherwise
    one request could truncate another session's in-progress ``.tmp``.
    The server mints a fresh id and leaves the named file untouched."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse('api:file_asset_v1')
    # A resumable session leaves a partial ``.tmp`` on disk.
    session = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-3/8'},
    )
    assert session.status_code == status.HTTP_200_OK
    victim_id = session.data['upload_id']

    # A single-shot upload that tries to reuse that id must not touch it.
    other = api_client.post(
        url,
        data={
            'file_upload': SimpleUploadedFile(
                'other.png', b'ZZZZZZZZ', content_type='image/png'
            )
        },
        headers={'X-Upload-Id': victim_id},
    )
    assert other.status_code == status.HTTP_200_OK
    assert other.data['upload_id'] != victim_id
    assert other.data['uri'] != session.data['uri']
    # The resumable session's file kept its original bytes.
    with open(session.data['uri'], 'rb') as f:
        assert f.read() == b'AAAA'


@pytest.mark.django_db
def test_recover_invalid_archive_warns_not_error(
    api_client: APIClient,
) -> None:
    """An operator uploading a non-backup file (here: not a gzip) is
    input validation, not a bug — the endpoint must 400 and log at
    warning, not logger.exception (which pages Sentry as an error,
    Sentry ANTHIAS-3W)."""
    import tarfile

    from django.core.files.uploadedfile import SimpleUploadedFile

    with (
        mock.patch('anthias_server.api.views.mixins.ViewerPublisher'),
        mock.patch(
            'anthias_server.api.views.mixins.open',
            mock.mock_open(),
            create=True,
        ),
        mock.patch(
            'anthias_server.api.views.mixins.path.isfile', return_value=False
        ),
        mock.patch(
            'anthias_server.api.views.mixins.backup_helper.recover',
            side_effect=tarfile.ReadError('not a gzip file'),
        ),
        mock.patch('anthias_server.api.views.mixins.logger') as mock_logger,
    ):
        response = api_client.post(
            reverse('api:recover_v1'),
            data={
                'backup_upload': SimpleUploadedFile(
                    'backup.tar.gz',
                    b'\n\nnot a real gzip',
                    content_type='application/x-tar',
                )
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'backup_upload' in response.data
    mock_logger.warning.assert_called_once()
    mock_logger.exception.assert_not_called()


@pytest.mark.django_db
def test_recover_streams_large_upload_to_disk(
    api_client: APIClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """The restore endpoint must stream the uploaded backup to disk in
    chunks, not ``read()`` the whole archive into RAM (which OOM-kills
    the worker on a Pi restoring a multi-GB backup). Upload content
    larger than one chunk and assert the staged file the recover step
    sees is the complete, byte-identical upload.

    The view stages under a relative ``static/`` dir, so run from a tmp
    cwd that pytest cleans up rather than polluting the checkout.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'static').mkdir()

    # > 64 KiB so file_upload.chunks() yields multiple chunks and the
    # streaming loop is actually exercised.
    payload = bytes(range(256)) * 1024  # 256 KiB, non-trivial content
    captured: dict[str, bytes] = {}

    def fake_recover(location: str) -> None:
        with open(location, 'rb') as staged:
            captured['content'] = staged.read()

    with (
        mock.patch('anthias_server.api.views.mixins.ViewerPublisher'),
        mock.patch(
            'anthias_server.api.views.mixins.backup_helper.recover',
            side_effect=fake_recover,
        ),
    ):
        response = api_client.post(
            reverse('api:recover_v1'),
            data={
                'backup_upload': SimpleUploadedFile(
                    'backup.tar.gz',
                    payload,
                    content_type='application/x-tar',
                )
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured['content'] == payload


@pytest.mark.django_db
def test_playlist_order(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    playlist_order_url = reverse('api:playlist_order_v1')

    for asset_name in ['Asset #1', 'Asset #2', 'Asset #3']:
        Asset.objects.create(
            **{
                **ASSET_CREATION_DATA,
                'name': asset_name,
            }
        )

    assert all(asset.play_order == 0 for asset in Asset.objects.all())

    asset_1, asset_2, asset_3 = Asset.objects.all()
    asset_ids = [asset_1.asset_id, asset_2.asset_id, asset_3.asset_id]

    response = api_client.post(
        playlist_order_url, data={'ids': ','.join(asset_ids)}
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    for asset in [asset_1, asset_2, asset_3]:
        asset.refresh_from_db()

    assert asset_1.play_order == 0
    assert asset_2.play_order == 1
    assert asset_3.play_order == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    'command',
    [
        'next',
        'previous',
        'asset&6ee2394e760643748b9353f06f405424',
    ],
)
@mock.patch(
    'anthias_server.api.views.v1.ViewerPublisher.send_to_viewer',
    return_value=None,
)
def test_assets_control(
    send_to_viewer_mock: Any,
    command: str,
    api_client: APIClient,
    isolated_asset_dir: None,
) -> None:
    assets_control_url = reverse('api:assets_control_v1', args=[command])
    response = api_client.get(assets_control_url)

    assert response.status_code == status.HTTP_200_OK
    assert send_to_viewer_mock.call_count == 1
    assert send_to_viewer_mock.call_args[0][0] == command
    assert response.data == 'Asset switched'


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.reboot_anthias.apply_async',
    side_effect=(lambda: None),
)
def test_reboot(
    reboot_anthias_mock: Any,
    api_client: APIClient,
    isolated_asset_dir: None,
) -> None:
    reboot_url = reverse('api:reboot_v1')
    response = api_client.post(reboot_url)

    assert response.status_code == status.HTTP_200_OK
    assert reboot_anthias_mock.call_count == 1


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.mixins.shutdown_anthias.apply_async',
    side_effect=(lambda: None),
)
def test_shutdown(
    shutdown_anthias_mock: Any,
    api_client: APIClient,
    isolated_asset_dir: None,
) -> None:
    shutdown_url = reverse('api:shutdown_v1')
    response = api_client.post(shutdown_url)

    assert response.status_code == status.HTTP_200_OK
    assert shutdown_anthias_mock.call_count == 1


@pytest.mark.django_db
@mock.patch(
    'anthias_server.api.views.v1.ViewerPublisher.send_to_viewer',
    return_value=None,
)
def test_viewer_current_asset(
    send_to_viewer_mock: Any,
    api_client: APIClient,
    isolated_asset_dir: None,
) -> None:
    asset = Asset.objects.create(
        **{
            **ASSET_CREATION_DATA,
            'is_enabled': 1,
        }
    )
    asset_id = asset.asset_id

    recv_json_mock = mock.MagicMock(
        return_value={'current_asset_id': asset_id}
    )
    with mock.patch(
        'anthias_server.api.views.v1.ReplyCollector.recv_json', recv_json_mock
    ):
        viewer_current_asset_url = reverse('api:viewer_current_asset_v1')
        response = api_client.get(viewer_current_asset_url)
        data = response.data

        assert response.status_code == status.HTTP_200_OK
        assert send_to_viewer_mock.call_count == 1

        # The view generates a UUID, embeds it in the command as
        # ``current_asset_id&<uuid>`` and waits on the reply keyed
        # by the same UUID. Pin that round-trip down so a future
        # refactor can't silently desync the two halves of the
        # request/reply pair (which would deadlock the request
        # until the 2s recv timeout fires).
        (sent_command,) = send_to_viewer_mock.call_args[0]
        assert sent_command.startswith('current_asset_id&')
        sent_corr_id = sent_command.split('&', 1)[1]

        assert recv_json_mock.call_count == 1
        recv_corr_id = recv_json_mock.call_args[0][0]
        assert recv_corr_id == sent_corr_id

        assert data['asset_id'] == asset_id
        assert data['is_active'] == 1


# ---------------------------------------------------------------------------
# Streaming upload / asset-content (issue #3345)
# ---------------------------------------------------------------------------
#
# Both endpoints used to materialise a whole media file in RAM to serve
# one request, which is enough to OOM any board under
# ``LOW_RAM_THRESHOLD_KB``. These pin the streaming behaviour AND the
# wire format, which must not move: the JSON body is part of the v1 and
# v2 contract.


def _pieces(response: Any) -> list[bytes]:
    """The body as the individual pieces the view yielded.

    The response carries an *async* iterator (see
    ``test_asset_content_is_async_iterable``), which the synchronous
    test client can only drain through ``async_to_sync``; Django warns
    about that and the warning is the test client's problem, not the
    view's.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='StreamingHttpResponse')
        return list(response)


def _body(response: Any) -> bytes:
    return b''.join(_pieces(response))


def _make_file_asset(name: str, body: bytes) -> Asset:
    asset_file = Path(anthias_settings['assetdir']) / 'clip.png'
    asset_file.write_bytes(body)
    return Asset.objects.create(
        name=name,
        uri=str(asset_file),
        mimetype='image',
        is_enabled=False,
        duration=10,
    )


class _NoSlurpUpload:
    """An uploaded file that refuses to hand over its whole body at once.

    Delegates everything to the real ``UploadedFile`` except an
    unbounded ``read()`` — the call this fix removed, and the one that
    costs a full copy of the asset in RAM. ``chunks()`` internally
    issues *bounded* reads, which are exactly what we want, so the
    distinction the assertion needs is size-of-read, not read-at-all.
    """

    def __init__(self, wrapped: UploadedFile[Any]) -> None:
        self._wrapped = wrapped
        self.chunk_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise AssertionError('unbounded read() of the upload body')
        return cast(bytes, self._wrapped.read(size))

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]:
        self.chunk_calls += 1
        return cast(Iterator[bytes], self._wrapped.chunks(chunk_size))


@pytest.mark.django_db
def test_file_asset_upload_never_reads_whole_body_into_memory(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """The single-shot upload path must copy the body with ``chunks()``.

    ``data = file_upload.read()`` was the bug: one full copy of the
    asset in RAM before a single byte reached the disk. Asserting on
    the access pattern rather than on a memory number keeps this
    deterministic — a reintroduced slurp fails here even on a machine
    with RAM to spare.

    The proxy is swapped in on the object the *view* receives, not the
    one the test client sends: those are different objects, and the
    client is entitled to read its own copy while encoding the request.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    payload = b'abc' * 5000
    proxies: list[_NoSlurpUpload] = []
    original_post = mixins.FileAssetViewMixin.post

    def spying_post(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        # Touching ``request.data`` first forces the multipart parse,
        # so the entry we overwrite is the parsed one.
        proxy = _NoSlurpUpload(request.data['file_upload'])
        proxies.append(proxy)
        request.data['file_upload'] = proxy
        return original_post(self, request, *args, **kwargs)

    with mock.patch.object(mixins.FileAssetViewMixin, 'post', spying_post):
        response = api_client.post(
            reverse('api:file_asset_v1'),
            data={
                'file_upload': SimpleUploadedFile(
                    'clip.png', payload, content_type='image/png'
                )
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert proxies[0].chunk_calls == 1
    with open(response.data['uri'], 'rb') as f:
        assert f.read() == payload


@pytest.mark.django_db
def test_file_asset_content_range_still_validates_chunk_length(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """The range-length check now consults the size the multipart
    parser recorded instead of ``len(body)``; it must still reject a
    chunk whose length contradicts the declared range."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = api_client.post(
        reverse('api:file_asset_v1'),
        data={
            'file_upload': SimpleUploadedFile(
                'clip.png', b'AAAA', content_type='image/png'
            )
        },
        headers={'Content-Range': 'bytes 0-1/2'},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@pytest.mark.parametrize(
    'size',
    # Clustered around the 3-byte base64 group boundary: the streaming
    # encoder only splices correctly on multiples of 3, so an
    # off-by-one emits mid-stream ``=`` padding and corrupts the asset.
    [0, 1, 2, 3, 4, 5, 6, 7, B64_STREAM_CHUNK_SIZE + 1],
)
def test_asset_content_round_trips_exactly(
    api_client: APIClient, isolated_asset_dir: None, size: int
) -> None:
    """Whatever the size, the base64 in the response decodes back to
    the file on disk byte for byte."""
    body = (bytes(range(256)) * (size // 256 + 1))[:size]
    asset = _make_file_asset('clip.png', body)

    response = api_client.get(_get_asset_content_url(asset.asset_id))

    assert response.status_code == status.HTTP_200_OK
    payload = json.loads(_body(response))
    assert base64.b64decode(payload['content'], validate=True) == body


@pytest.mark.django_db
@pytest.mark.parametrize(
    'filename',
    # An empty name, and names carrying the very JSON punctuation the
    # envelope is spliced on — both are places a naive splice picks the
    # wrong field or emits invalid JSON.
    ['clip.png', '', 'oddly "quoted" ünï.png', '{"content":""}.png'],
)
def test_asset_content_body_matches_the_unstreamed_json(
    api_client: APIClient, isolated_asset_dir: None, filename: str
) -> None:
    """The streamed bytes are exactly what rendering the equivalent
    dict produced before: same fields, same order, same escaping.

    That JSON is the v1/v2 wire contract; streaming is an
    implementation detail and must not show through.
    """
    body = b'hello \xc3\xa9 world'
    asset = _make_file_asset(filename, body)

    response = api_client.get(_get_asset_content_url(asset.asset_id))
    streamed = _body(response)

    assert streamed == JSONRenderer().render(
        {
            'type': 'file',
            'filename': filename,
            'content': base64.b64encode(body).decode(),
            'mimetype': guess_type(filename)[0] or 'application/octet-stream',
        }
    )
    assert response['Content-Type'] == 'application/json'
    assert int(response['Content-Length']) == len(streamed)


@pytest.mark.django_db
def test_asset_content_streams_in_bounded_pieces(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """No single piece of the body — and so no single allocation —
    scales with the asset.

    This is the guard that matters on a 1 GB board. A response yielded
    as one big piece would satisfy every round-trip assertion above
    while reintroducing the bug in full.
    """
    asset = _make_file_asset(
        'clip.png', b'\0' * (B64_STREAM_CHUNK_SIZE * 3 + 7)
    )

    response = api_client.get(_get_asset_content_url(asset.asset_id))
    pieces = _pieces(response)

    assert len(pieces) > 3
    # base64 inflates 3 bytes to 4, so one chunk's worth of output is
    # 4/3 of the read size; the envelope rides on top of that.
    assert max(map(len, pieces)) <= B64_STREAM_CHUNK_SIZE * 4 // 3 + 1024


@pytest.mark.django_db
def test_asset_content_is_async_iterable(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """The response must carry an *async* iterator.

    Under ASGI — how anthias-server actually runs — Django drains a
    synchronous ``streaming_content`` through ``sync_to_async(list)``
    before sending a single byte, which silently restores full
    buffering while every other test here still passes.
    """
    asset = _make_file_asset('clip.png', b'hello')

    response = api_client.get(_get_asset_content_url(asset.asset_id))

    assert cast(StreamingHttpResponse, response).is_async


@pytest.mark.django_db
def test_asset_content_404s_when_file_vanishes_before_streaming(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """A file deleted between the ``isfile`` probe and the open is a
    clean 404, not a 200 whose body dies after the headers ship."""
    asset = _make_file_asset('clip.png', b'hello')
    real_open = open

    def open_but_gone(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == asset.uri:
            raise FileNotFoundError(asset.uri)
        return real_open(file, *args, **kwargs)

    with mock.patch(
        'anthias_server.api.views.mixins.open', open_but_gone, create=True
    ):
        response = api_client.get(_get_asset_content_url(asset.asset_id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_asset_content_releases_the_fd_when_the_response_closes(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """An aborted download must not pin the asset's file descriptor.

    The streaming handle outlives the view, so something has to close
    it. Django only auto-registers a closer when ``streaming_content``
    exposes ``close``, and an async generator exposes ``aclose`` — so
    the registration silently does not happen and ``response.close()``,
    the only cleanup Django runs for an abandoned streaming response,
    would leave the fd and its inode pinned until GC.
    """
    asset = _make_file_asset('clip.png', b'x' * 4096)

    def open_fds() -> list[str]:
        found = []
        for fd in os.listdir('/proc/self/fd'):
            with suppress(OSError):
                if os.readlink(f'/proc/self/fd/{fd}') == asset.uri:
                    found.append(fd)
        return found

    response = api_client.get(_get_asset_content_url(asset.asset_id))
    assert open_fds(), 'expected the view to hold the asset open'

    # Never drain the body — exactly what an aborted download looks
    # like from the server's side.
    response.close()

    assert open_fds() == []


@pytest.mark.django_db
@pytest.mark.parametrize('errno_code', [errno.EIO, errno.EACCES, errno.EMFILE])
def test_asset_content_does_not_disguise_io_errors_as_404(
    api_client: APIClient, isolated_asset_dir: None, errno_code: int
) -> None:
    """Only a missing file is a 404.

    A failing SD card (EIO), a permissions mistake (EACCES) or fd
    exhaustion (EMFILE) dressed up as "no such asset" is a clean 404
    that tells Sentry nothing and that a backup client skips over
    without ever reporting a problem. Those have to surface as errors.
    """
    asset = _make_file_asset('clip.png', b'hello')
    real_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == asset.uri:
            raise OSError(errno_code, os.strerror(errno_code))
        return real_open(file, *args, **kwargs)

    with mock.patch(
        'anthias_server.api.views.mixins.open', failing_open, create=True
    ):
        response = api_client.get(_get_asset_content_url(asset.asset_id))

    assert response.status_code != status.HTTP_404_NOT_FOUND
    assert response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.django_db
def test_asset_content_honours_the_indent_media_type_parameter(
    api_client: APIClient, isolated_asset_dir: None
) -> None:
    """``application/json; indent=4`` must format both shapes alike.

    The URL branch is still a DRF ``Response`` and honours the
    parameter; the file branch renders its own envelope, so it has to
    be handed the negotiated media type or one endpoint would format
    its two shapes differently.
    """
    body = b'hi'
    file_asset = _make_file_asset('clip.png', body)
    url_asset = Asset.objects.create(
        name='somewhere',
        uri='https://anthias.screenly.io',
        mimetype='webpage',
        is_enabled=False,
        duration=10,
    )
    accept = 'application/json; indent=4'

    file_response = api_client.get(
        _get_asset_content_url(file_asset.asset_id), HTTP_ACCEPT=accept
    )
    url_response = api_client.get(
        _get_asset_content_url(url_asset.asset_id), HTTP_ACCEPT=accept
    )

    streamed = _body(file_response)
    assert streamed == JSONRenderer().render(
        {
            'type': 'file',
            'filename': 'clip.png',
            'content': base64.b64encode(body).decode(),
            'mimetype': 'image/png',
        },
        accepted_media_type=accept,
    )
    # Both branches pretty-printed, and the advertised length still
    # describes the indented body.
    assert streamed.startswith(b'{\n    ')
    assert url_response.content.startswith(b'{\n    ')
    assert int(file_response['Content-Length']) == len(streamed)
