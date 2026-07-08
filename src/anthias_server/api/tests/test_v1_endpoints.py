"""
Tests for V1 API endpoints.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from anthias_server.app.models import Asset
from anthias_server.api.tests.test_common import ASSET_CREATION_DATA
from anthias_server.settings import settings as anthias_settings


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def cleanup_asset_dir() -> Iterator[None]:
    try:
        yield
    finally:
        asset_directory_path = Path(anthias_settings['assetdir'])
        for file in asset_directory_path.iterdir():
            file.unlink()


def _get_asset_content_url(asset_id: str) -> str:
    return str(reverse('api:asset_content_v1', args=[asset_id]))


@pytest.mark.django_db
def test_asset_content(api_client: APIClient, cleanup_asset_dir: None) -> None:
    asset = Asset.objects.create(**ASSET_CREATION_DATA)
    asset_id = asset.asset_id

    response = api_client.get(_get_asset_content_url(asset_id))
    data = response.data

    assert response.status_code == status.HTTP_200_OK
    assert data['type'] == 'url'
    assert data['url'] == 'https://anthias.screenly.io'


@pytest.mark.django_db
def test_file_asset(api_client: APIClient, cleanup_asset_dir: None) -> None:
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
def test_file_asset_disk_full_returns_507(
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None, header: str
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    api_client: APIClient, cleanup_asset_dir: None
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
    cleanup_asset_dir: None,
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
    cleanup_asset_dir: None,
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
    cleanup_asset_dir: None,
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
    cleanup_asset_dir: None,
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
