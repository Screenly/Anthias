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
    project_base_path = django_settings.BASE_DIR
    image_path = os.path.join(
        project_base_path,
        'static/img/standby.png',
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
