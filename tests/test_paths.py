"""
anthias_common.paths keeps paths built from requests, stored asset rows
and restored backups inside the directory they belong to.
"""

import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from anthias_common.paths import (
    PathOutsideBaseError,
    is_within,
    resolve_within,
)


def test_a_plain_child_is_within(tmp_path: Path) -> None:
    assert is_within(str(tmp_path), str(tmp_path / 'clip.mp4'))


def test_the_base_itself_is_not_within(tmp_path: Path) -> None:
    assert not is_within(str(tmp_path), str(tmp_path))


def test_dot_dot_that_starts_with_the_base_is_not_within(
    tmp_path: Path,
) -> None:
    # The bug a bare prefix test lets through: the string starts with
    # the base, the file it names does not live there.
    sneaky = f'{tmp_path}/../outside.db'

    assert sneaky.startswith(str(tmp_path))
    assert not is_within(str(tmp_path), sneaky)


def test_a_sibling_sharing_the_prefix_is_not_within(tmp_path: Path) -> None:
    base = tmp_path / 'anthias_assets'
    base.mkdir()

    assert not is_within(str(base), str(tmp_path / 'anthias_assets-old' / 'x'))


def test_a_symlink_leading_out_is_not_within(tmp_path: Path) -> None:
    base = tmp_path / 'assets'
    base.mkdir()
    outside = tmp_path / 'secret'
    outside.write_text('x')
    (base / 'link').symlink_to(outside)

    assert not is_within(str(base), str(base / 'link'))


def test_resolve_within_returns_the_normalised_join(tmp_path: Path) -> None:
    resolved = resolve_within(str(tmp_path), '.uploads', 'a.part')

    assert resolved == os.path.join(str(tmp_path), '.uploads', 'a.part')


def test_resolve_within_keeps_a_symlinked_base_as_configured(
    tmp_path: Path,
) -> None:
    # Asset rows store paths under the configured dir; returning the
    # resolved form would change them on installs that reach the dir
    # through a back-compat symlink.
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'configured'
    link.symlink_to(real)

    resolved = resolve_within(str(link), 'clip.mp4')

    assert resolved == os.path.join(str(link), 'clip.mp4')


@pytest.mark.parametrize(
    'part', ['../escape', '/etc/passwd', 'a/../../escape', '']
)
def test_resolve_within_refuses_an_escape(tmp_path: Path, part: str) -> None:
    with pytest.raises(PathOutsideBaseError):
        resolve_within(str(tmp_path), part)


@pytest.mark.django_db
def test_deleting_an_asset_never_removes_a_file_outside_the_asset_dir(
    tmp_path: Path,
) -> None:
    """A row restored from a backup can carry any uri. One that starts
    with the asset dir but climbs out of it must not take a file with
    it when the asset is deleted."""
    from anthias_server.app.helpers import delete_asset_with_file
    from anthias_server.app.models import Asset
    from anthias_server.settings import settings as anthias_settings

    assetdir = tmp_path / 'anthias_assets'
    assetdir.mkdir()
    victim = tmp_path / 'anthias.db'
    victim.write_text('do not delete')
    asset = Asset.objects.create(
        asset_id='traversal',
        name='traversal',
        uri=f'{assetdir}/../anthias.db',
        mimetype='image',
    )

    with mock.patch.dict(anthias_settings, {'assetdir': str(assetdir)}):
        delete_asset_with_file(asset, nudge_viewer=False)

    assert victim.exists()
    assert not Asset.objects.filter(asset_id='traversal').exists()


@pytest.mark.django_db
def test_deleting_an_asset_still_removes_its_own_file(tmp_path: Path) -> None:
    from anthias_server.app.helpers import delete_asset_with_file
    from anthias_server.app.models import Asset
    from anthias_server.settings import settings as anthias_settings

    own = tmp_path / 'clip.mp4'
    own.write_bytes(b'x')
    asset = Asset.objects.create(
        asset_id='own', name='own', uri=str(own), mimetype='video'
    )

    with mock.patch.dict(anthias_settings, {'assetdir': str(tmp_path)}):
        delete_asset_with_file(asset, nudge_viewer=False)

    assert not own.exists()


@pytest.mark.django_db
def test_chunked_upload_errors_answer_with_fixed_messages(
    tmp_path: Any,
) -> None:
    """The body comes from the fixed table, keyed by the error, never
    from rendering the exception."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    from django.urls import reverse

    from anthias_server.app.views import _CHUNKED_UPLOAD_ERRORS
    from anthias_server.settings import settings as anthias_settings

    with mock.patch.dict(anthias_settings, {'assetdir': str(tmp_path)}):
        response = Client().post(
            reverse('anthias_app:assets_upload'),
            data={
                'file_upload': SimpleUploadedFile(
                    'clip.mp4', b'01234', content_type='video/mp4'
                ),
            },
            headers={
                'HX-Request': 'true',
                'Content-Range': 'bytes 0-4/10',
                'X-Upload-Id': 'not-hex',
            },
        )

    message, status = _CHUNKED_UPLOAD_ERRORS['malformed_id']
    assert response.status_code == status
    assert response.json() == {'error': message}
