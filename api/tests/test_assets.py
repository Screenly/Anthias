"""
Tests for asset-related API endpoints.
"""

from typing import Any
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.tests.test_common import (
    ASSET_CREATION_DATA,
    ASSET_UPDATE_DATA_V1_2,
    ASSET_UPDATE_DATA_V2,
    get_request_data,
)


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _get_assets(client: APIClient, version: str) -> Any:
    asset_list_url = reverse(f'api:asset_list_{version}')
    response = client.get(asset_list_url)

    assert response.status_code == status.HTTP_200_OK

    return response.data


def _create_asset(
    client: APIClient, data: dict[str, Any], version: str
) -> Any:
    asset_list_url = reverse(f'api:asset_list_{version}')
    return client.post(
        asset_list_url, data=get_request_data(data, version)
    ).data


def _update_asset(
    client: APIClient,
    asset_id: str,
    data: dict[str, Any],
    version: str,
) -> Any:
    return client.put(
        reverse(f'api:asset_detail_{version}', args=[asset_id]),
        data=get_request_data(data, version),
    ).data


def _get_asset(client: APIClient, asset_id: str, version: str) -> Any:
    url = reverse(f'api:asset_detail_{version}', args=[asset_id])
    return client.get(url).data


def _delete_asset(client: APIClient, asset_id: str, version: str) -> Any:
    url = reverse(f'api:asset_detail_{version}', args=[asset_id])
    return client.delete(url)


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_get_assets_when_first_time_setup_should_initially_return_empty(
    api_client: APIClient, version: str
) -> None:  # noqa: E501
    asset_list_url = reverse(f'api:asset_list_{version}')
    response = api_client.get(asset_list_url)
    assets = response.data

    assert response.status_code == status.HTTP_200_OK
    assert len(assets) == 0


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_create_asset_should_return_201(
    api_client: APIClient, version: str
) -> None:
    asset_list_url = reverse(f'api:asset_list_{version}')
    response = api_client.post(
        asset_list_url, data=get_request_data(ASSET_CREATION_DATA, version)
    )

    assert response.status_code == status.HTTP_201_CREATED

    assert response.data['name'] == 'Anthias'
    assert response.data['uri'] == 'https://anthias.screenly.io'
    assert response.data['is_enabled'] == 0
    assert response.data['nocache'] == 0
    assert response.data['play_order'] == 0
    assert response.data['skip_asset_check'] == 0


@pytest.mark.django_db
@mock.patch('api.serializers.mixins.rename')
@mock.patch('api.serializers.mixins.validate_uri')
def test_create_video_asset_v2_with_non_zero_duration_should_fail(
    mock_validate_uri: Any, mock_rename: Any, api_client: APIClient
) -> None:
    """Test that v2 rejects video assets with non-zero duration."""
    mock_validate_uri.return_value = True
    asset_list_url = reverse('api:asset_list_v2')

    test_data = {
        'name': 'Test Video',
        'uri': '/data/anthias_assets/video.mp4',
        'start_date': '2019-08-24T14:15:22Z',
        'end_date': '2029-08-24T14:15:22Z',
        'duration': 30,
        'mimetype': 'video',
        'is_enabled': True,
        'nocache': False,
        'play_order': 0,
        'skip_asset_check': False,
    }

    response = api_client.post(asset_list_url, data=test_data, format='json')

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert 'Duration must be zero for video assets' in str(response.data)

    assert mock_rename.call_count == 1
    assert mock_validate_uri.call_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_get_assets_after_create_should_return_1_asset(
    api_client: APIClient, version: str
) -> None:
    _create_asset(api_client, ASSET_CREATION_DATA, version)

    assets = _get_assets(api_client, version)
    assert len(assets) == 1


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_get_asset_by_id_should_return_asset(
    api_client: APIClient, version: str
) -> None:
    expected_asset = _create_asset(api_client, ASSET_CREATION_DATA, version)
    asset_id = expected_asset['asset_id']
    actual_asset = _get_asset(api_client, asset_id, version)

    assert expected_asset == actual_asset


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_update_asset_should_return_updated_asset(
    api_client: APIClient, version: str
) -> None:
    expected_asset = _create_asset(api_client, ASSET_CREATION_DATA, version)
    asset_id = expected_asset['asset_id']

    if version == 'v2':
        data = ASSET_UPDATE_DATA_V2
    else:
        data = ASSET_UPDATE_DATA_V1_2

    updated_asset = _update_asset(
        api_client,
        asset_id,
        data=data,
        version=version,
    )

    assert updated_asset['name'] == 'Anthias'
    assert updated_asset['uri'] == 'https://anthias.screenly.io'
    assert updated_asset['duration'] == data['duration']
    assert updated_asset['is_enabled'] == data['is_enabled']
    assert updated_asset['play_order'] == data['play_order']


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_delete_asset_should_return_204(
    api_client: APIClient, version: str
) -> None:
    asset = _create_asset(api_client, ASSET_CREATION_DATA, version)
    asset_id = asset['asset_id']

    response = _delete_asset(api_client, asset_id, version)
    assets = api_client.get(reverse('api:asset_list_v1_1')).data

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert len(assets) == 0
