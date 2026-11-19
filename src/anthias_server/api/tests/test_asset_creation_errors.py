"""
Asset-creation refusals are client errors: 400, never 500.

There used to be two ``AssetCreationError`` classes, one in
``api.errors`` (raised by the create serializers) and one in
``api.helpers`` (caught by the create views and the content importer).
Anything the serializer raised slipped past every handler, so a
non-zero duration on a video or an unreachable URL came back from the
REST API as a 500, and from the importer as an unhandled exception. The
v1/v1.1 serializer raised a bare ``RuntimeError`` for an unreachable
URL, with the same result.

The response body is unchanged by the fix (``{"error": ...}``); only
the status is now the 400 the views always meant to send.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from anthias_server.api.tests.test_common import (
    ASSET_CREATION_DATA,
    get_request_data,
)


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1_2', 'v2'])
@mock.patch('anthias_server.api.serializers.mixins.rename')
@mock.patch('anthias_server.api.serializers.mixins.validate_uri')
def test_video_with_a_non_zero_duration_is_a_400(
    mock_validate_uri: Any,
    _mock_rename: Any,
    api_client: APIClient,
    version: str,
) -> None:
    payload = {
        **ASSET_CREATION_DATA,
        'uri': '/data/anthias_assets/video.mp4',
        'mimetype': 'video',
        'duration': 30,
    }

    response = api_client.post(
        reverse(f'api:asset_list_{version}'), data=payload, format='json'
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        'error': 'Duration must be zero for video assets.'
    }


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_an_unreachable_url_is_a_400(
    api_client: APIClient, version: str
) -> None:
    from anthias_server.app.models import Asset

    with (
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=True,
        ),
        mock.patch(
            'anthias_server.api.serializers.v1_1.url_fails',
            return_value=True,
        ),
    ):
        response = api_client.post(
            reverse(f'api:asset_list_{version}'),
            data=get_request_data(ASSET_CREATION_DATA, version),
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        'error': 'Could not retrieve file. Check the asset URL.'
    }
    assert Asset.objects.count() == 0


@pytest.mark.django_db
def test_the_importer_reports_a_refusal_instead_of_crashing() -> None:
    """The content importer catches the same error and turns it into a
    ProviderImportError the integration surfaces to the operator."""
    from anthias_server.lib.integrations.base import ProviderImportError
    from anthias_server.lib.integrations.ingest import create_webpage_asset

    now = datetime.now(UTC)
    with (
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=True,
        ),
        pytest.raises(ProviderImportError, match='Could not retrieve'),
    ):
        create_webpage_asset(
            provider_key='test',
            remote_id='1',
            name='unreachable',
            url='https://unreachable.example.com',
            duration=10,
            start_date=now,
            end_date=now + timedelta(days=1),
            enable=False,
        )


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1'])
@pytest.mark.parametrize(
    'body',
    [{'name': 'no model field'}, {'model': 'not json'}],
)
def test_a_v1_body_that_is_not_an_asset_is_a_400(
    api_client: APIClient, version: str, body: dict[str, str]
) -> None:
    response = api_client.post(reverse(f'api:asset_list_{version}'), data=body)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_there_is_one_asset_creation_error() -> None:
    from anthias_server.api import errors, helpers

    assert not hasattr(helpers, 'AssetCreationError')
    assert issubclass(errors.AssetCreationError, Exception)
