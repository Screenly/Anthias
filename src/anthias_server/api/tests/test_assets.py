"""
Tests for asset-related API endpoints.
"""

from typing import Any
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from anthias_server.api.tests.test_common import (
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
) -> None:
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
@mock.patch('anthias_server.api.serializers.mixins.rename')
@mock.patch('anthias_server.api.serializers.mixins.validate_uri')
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


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_unknown_asset_id_returns_404(
    api_client: APIClient, version: str
) -> None:
    # Asset.objects.get(asset_id=...) used to bubble DoesNotExist as a
    # 500 here; views now use get_object_or_404 so a deleted or
    # mistyped id maps cleanly to 404 across every API version.
    detail_url = reverse(f'api:asset_detail_{version}', args=['nonexistent'])
    assert api_client.get(detail_url).status_code == status.HTTP_404_NOT_FOUND
    assert (
        api_client.delete(detail_url).status_code == status.HTTP_404_NOT_FOUND
    )


@pytest.fixture
def v2_asset_detail_url(api_client: APIClient) -> str:
    asset = api_client.post(
        reverse('api:asset_list_v2'),
        data=ASSET_CREATION_DATA,
    ).data
    return reverse('api:asset_detail_v2', args=[asset['asset_id']])


@pytest.mark.django_db
def test_v2_update_with_empty_play_days_rejected(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    response = api_client.put(
        v2_asset_detail_url,
        data={**ASSET_UPDATE_DATA_V2, 'play_days': []},
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'play_days' in response.data


@pytest.mark.django_db
def test_v2_update_with_partial_time_window_rejected(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    response = api_client.put(
        v2_asset_detail_url,
        data={
            **ASSET_UPDATE_DATA_V2,
            'play_time_from': '09:00:00',
            'play_time_to': None,
        },
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'play_time_to' in response.data


@pytest.mark.django_db
def test_v2_update_with_full_time_window_accepted(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    response = api_client.put(
        v2_asset_detail_url,
        data={
            **ASSET_UPDATE_DATA_V2,
            'play_time_from': '09:00:00',
            'play_time_to': '17:00:00',
        },
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data['play_time_from'] == '09:00:00'
    assert response.data['play_time_to'] == '17:00:00'


@pytest.mark.django_db
def test_v2_create_with_play_days_round_trips(
    api_client: APIClient,
) -> None:
    # Regression: serializer.data must round-trip play_days through
    # AssetListViewV2.post -> Asset.objects.create(**serializer.data).
    # Previously _normalise_play_days returned a JSON string, which
    # ListField.to_representation could not iterate as ints.
    response = api_client.post(
        reverse('api:asset_list_v2'),
        data={
            **ASSET_CREATION_DATA,
            'play_days': [3, 1, 1, 5],
            'play_time_from': '09:00:00',
            'play_time_to': '17:00:00',
        },
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['play_days'] == [1, 3, 5]
    assert response.data['play_time_from'] == '09:00:00'
    assert response.data['play_time_to'] == '17:00:00'


@pytest.mark.django_db
def test_v2_post_with_refresh_interval_round_trips(
    api_client: APIClient,
) -> None:
    """POST round-trip for the webpage auto-refresh interval.

    The interval lives inside ``Asset.metadata`` (so the upload
    pipeline's read-only stance on metadata is preserved) but is
    surfaced as a top-level ``refresh_interval_s`` field on the v2
    response via SerializerMethodField, and accepted as a write_only
    field on the create serializer. Verifies the value lands on the
    persisted row, not just on the response.
    """
    response = api_client.post(
        reverse('api:asset_list_v2'),
        data={**ASSET_CREATION_DATA, 'refresh_interval_s': 30},
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['refresh_interval_s'] == 30
    assert response.data['metadata'] == {'refresh_interval_s': 30}

    # GET the freshly created row through the read serializer to make
    # sure the response above isn't echoing the request — round-trip
    # through the DB confirms the post-create metadata save fired.
    asset_id = response.data['asset_id']
    detail = api_client.get(reverse('api:asset_detail_v2', args=[asset_id]))
    assert detail.status_code == status.HTTP_200_OK
    assert detail.data['refresh_interval_s'] == 30


@pytest.mark.django_db
def test_v2_post_without_refresh_interval_defaults_to_zero(
    api_client: APIClient,
) -> None:
    """A create without ``refresh_interval_s`` reads back as 0 (the
    "no auto-refresh" default), and ``metadata`` is left as an empty
    dict so the upload pipeline's other keys aren't shadowed by a
    stub entry."""
    response = api_client.post(
        reverse('api:asset_list_v2'),
        data=ASSET_CREATION_DATA,
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['refresh_interval_s'] == 0
    assert response.data['metadata'] == {}


@pytest.mark.django_db
def test_v2_patch_refresh_interval_round_trips(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    """PATCH a single ``refresh_interval_s`` (partial update) onto an
    existing webpage asset and read the new value back."""
    response = api_client.patch(
        v2_asset_detail_url,
        data={'refresh_interval_s': 45},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data['refresh_interval_s'] == 45
    assert response.data['metadata'] == {'refresh_interval_s': 45}


@pytest.mark.django_db
@pytest.mark.parametrize(
    'value',
    [-1, 86401],
    ids=['negative', 'over-24h-cap'],
)
def test_v2_patch_refresh_interval_out_of_range_rejected(
    api_client: APIClient, v2_asset_detail_url: str, value: int
) -> None:
    """Both bounds of the documented 0..REFRESH_INTERVAL_S_MAX range
    must 400 on write. ``-1`` is the just-below-zero case; ``86401``
    catches the typo (operator entered milliseconds, etc.) that the
    24h cap exists to prevent."""
    response = api_client.patch(
        v2_asset_detail_url,
        data={'refresh_interval_s': value},
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'refresh_interval_s' in response.data


@pytest.mark.django_db
def test_v2_patch_refresh_interval_preserves_pipeline_metadata(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    """Operator-driven refresh_interval_s edits must not stomp on the
    upload pipeline's metadata keys (original_ext, transcoded,
    error_message). Pre-seeding the row with a pipeline payload and
    then PATCHing the interval is the canonical regression for the
    "transcoded=true but file is original" desync that motivated
    making metadata read-only on the create/update serializers."""
    from anthias_server.app.models import Asset

    asset_id = v2_asset_detail_url.rstrip('/').rsplit('/', 1)[-1]
    asset = Asset.objects.get(asset_id=asset_id)
    asset.metadata = {'original_ext': '.heic', 'transcoded': False}
    asset.save(update_fields=['metadata'])

    response = api_client.patch(
        v2_asset_detail_url,
        data={'refresh_interval_s': 60},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data['metadata'] == {
        'original_ext': '.heic',
        'transcoded': False,
        'refresh_interval_s': 60,
    }


@pytest.mark.django_db
def test_v2_get_normalises_refresh_interval_in_metadata_too(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    """The top-level ``refresh_interval_s`` and the embedded
    ``metadata['refresh_interval_s']`` must agree on the response —
    a row with an out-of-range stored value would otherwise produce
    contradictory JSON (top-level clamped to 0, metadata still echoing
    -42), and a client reading metadata could re-submit the bad
    value. Other metadata keys (upload-pipeline state) pass
    through untouched."""
    from anthias_server.app.models import Asset

    asset_id = v2_asset_detail_url.rstrip('/').rsplit('/', 1)[-1]
    asset = Asset.objects.get(asset_id=asset_id)
    asset.metadata = {
        'refresh_interval_s': 999999,
        'original_ext': '.heic',
        'transcoded': True,
    }
    asset.save(update_fields=['metadata'])

    response = api_client.get(v2_asset_detail_url)
    assert response.status_code == status.HTTP_200_OK
    assert response.data['refresh_interval_s'] == 86400
    assert response.data['metadata']['refresh_interval_s'] == 86400
    # Pipeline-owned keys must pass through unchanged.
    assert response.data['metadata']['original_ext'] == '.heic'
    assert response.data['metadata']['transcoded'] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    'stored,expected',
    [
        (-42, 0),  # negative clamped up to 0
        (999999, 86400),  # huge value clamped down to 24h cap
    ],
)
def test_v2_get_clamps_out_of_range_refresh_interval(
    api_client: APIClient,
    v2_asset_detail_url: str,
    stored: int,
    expected: int,
) -> None:
    """The serializer's write path rejects out-of-range values, but a
    hand-edited row or a legacy import could leave a junk value in
    metadata. GET should clamp to the documented 0..86400 contract
    rather than echo whatever's in the column — a UI that round-tripped
    the raw value would let the operator save a value the next PATCH
    would 400 on."""
    from anthias_server.app.models import Asset

    asset_id = v2_asset_detail_url.rstrip('/').rsplit('/', 1)[-1]
    asset = Asset.objects.get(asset_id=asset_id)
    asset.metadata = {'refresh_interval_s': stored}
    asset.save(update_fields=['metadata'])

    response = api_client.get(v2_asset_detail_url)
    assert response.status_code == status.HTTP_200_OK
    assert response.data['refresh_interval_s'] == expected


@pytest.mark.django_db
def test_v2_patch_refresh_interval_zero_disables(
    api_client: APIClient, v2_asset_detail_url: str
) -> None:
    """0 stores explicitly; the viewer treats 0 the same as a missing
    key (no auto-refresh). Operators clearing the field from the edit
    modal POST 0 to disable rather than DELETE the metadata key, so
    the round-trip should accept 0 and surface 0 on read."""
    api_client.patch(
        v2_asset_detail_url,
        data={'refresh_interval_s': 30},
        format='json',
    )
    response = api_client.patch(
        v2_asset_detail_url,
        data={'refresh_interval_s': 0},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data['refresh_interval_s'] == 0


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_create_youtube_asset_dispatches_celery_task(
    api_client: APIClient, version: str
) -> None:
    """Every API version that accepts ``mimetype='youtube_asset'``
    must dispatch ``download_youtube_asset`` after persisting the
    row. v1.2 was previously missing this hop, so the row landed
    with ``is_processing=1`` and never finalized. Parametrize across
    every version so a future create-view regression for any of
    them fails this test instead of leaking into prod silently.
    """
    youtube_url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': youtube_url,
        'mimetype': 'youtube_asset',
        # Required for YouTube: the serializer sets duration=0 itself,
        # but the v1.1 serializer's input validation still needs a
        # 0/None to avoid the "explicit override" branch.
        'duration': 0,
    }

    asset_list_url = reverse(f'api:asset_list_{version}')
    with (
        mock.patch(
            'anthias_server.api.views.v1.dispatch_download'
        ) as v1_dispatch,
        mock.patch(
            'anthias_server.api.views.v1_1.dispatch_download'
        ) as v1_1_dispatch,
        mock.patch(
            'anthias_server.api.views.v1_2.dispatch_download'
        ) as v1_2_dispatch,
        mock.patch(
            'anthias_server.api.views.v2.dispatch_download'
        ) as v2_dispatch,
        # Skip the network probe — url_fails would be invoked on the
        # local mp4 destination, which is a no-op in practice but
        # avoids any future url_fails behaviour change leaking into
        # this test.
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
        mock.patch(
            'anthias_server.api.serializers.v1_1.url_fails',
            return_value=False,
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED
    asset_id = response.data['asset_id']

    dispatch_for_version = {
        'v1': v1_dispatch,
        'v1_1': v1_1_dispatch,
        'v1_2': v1_2_dispatch,
        'v2': v2_dispatch,
    }[version]
    dispatch_for_version.assert_called_once_with(asset_id, youtube_url)

    # The other versions' dispatchers must stay untouched — proves
    # we routed through the right view, not just any of them.
    for v, m in {
        'v1': v1_dispatch,
        'v1_1': v1_1_dispatch,
        'v1_2': v1_2_dispatch,
        'v2': v2_dispatch,
    }.items():
        if v != version:
            m.assert_not_called()


# ---------------------------------------------------------------------------
# Viewer wake-ups on mutation — issue #2430
# ---------------------------------------------------------------------------
#
# Delete / update must publish ``reload`` so the viewer can advance
# past the just-modified asset instead of finishing its originally-
# scheduled duration on screen. The viewer-side decision of whether to
# actually skip lives in _skip_if_current_asset_inactive (tested in
# tests/test_viewer.py); here we just assert the wake-up is sent.


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
@mock.patch('anthias_server.app.helpers.ViewerPublisher')
def test_delete_asset_publishes_reload(
    publisher_mock: Any, api_client: APIClient, version: str
) -> None:
    publisher_instance = mock.MagicMock()
    publisher_mock.get_instance.return_value = publisher_instance

    asset = _create_asset(api_client, ASSET_CREATION_DATA, version)
    response = _delete_asset(api_client, asset['asset_id'], version)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    publisher_instance.send_to_viewer.assert_called_once_with('reload')


@pytest.mark.django_db
@pytest.mark.parametrize(
    'version,publisher_target',
    [
        ('v1', 'anthias_server.api.views.v1.ViewerPublisher'),
        ('v1_1', 'anthias_server.api.views.v1_1.ViewerPublisher'),
        ('v1_2', 'anthias_server.api.helpers.ViewerPublisher'),
        ('v2', 'anthias_server.api.helpers.ViewerPublisher'),
    ],
)
def test_update_asset_publishes_reload(
    api_client: APIClient, version: str, publisher_target: str
) -> None:
    """v1.x put writes its own publish; v1_2/v2 share the helper."""
    with mock.patch(publisher_target) as publisher_mock:
        publisher_instance = mock.MagicMock()
        publisher_mock.get_instance.return_value = publisher_instance

        asset = _create_asset(api_client, ASSET_CREATION_DATA, version)
        data = (
            ASSET_UPDATE_DATA_V2 if version == 'v2' else ASSET_UPDATE_DATA_V1_2
        )

        _update_asset(api_client, asset['asset_id'], data, version)

        publisher_instance.send_to_viewer.assert_called_once_with('reload')


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1', 'v1_2', 'v2'])
def test_create_video_asset_dispatches_normalize_task(
    api_client: APIClient,
    version: str,
) -> None:
    """Every API version that accepts a local video upload must
    dispatch ``normalize_video_asset`` after persisting the row.

    Regression test for GH #2870: v1 and v1.1 originally skipped this
    dispatch, leaving the row in whatever codec the operator uploaded
    (e.g. MPEG-1) — the viewer's per-board passthrough set then
    silently dropped it from rotation forever. Parametrized so a
    future create-view regression for any version fails this test
    instead of leaking into production silently.
    """
    # ``stack`` lets the same test cover both serializer surfaces
    # (v1/v1.1 use ``serializers.v1_1.*`` symbols; v1.2/v2 use the
    # shared ``serializers.mixins.*`` symbols) without duplicating
    # the test body. Each version's serializer renames the staged
    # ``.tmp`` file into assetdir before persistence — for a test
    # without a real on-disk file we patch the rename out; same for
    # the url_fails reachability probe.
    local_video_uri = '/data/anthias_assets/test-video.mp4'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': local_video_uri,
        'mimetype': 'video',
        # v1.2 / v2 mixin requires duration=0 for video uploads (it
        # defers ffprobe to the normalize task). v1.1 also accepts
        # 0; we mock get_video_duration so its synchronous probe
        # doesn't try to ffprobe a non-existent path.
        'duration': 0,
        # v1.2 / v2 mixin's rename preserves ``ext`` on the destination
        # filename — v1 / v1.1 ignore it. Including it keeps both
        # branches happy.
        'ext': '.mp4',
    }
    asset_list_url = reverse(f'api:asset_list_{version}')

    with (
        mock.patch(
            'anthias_server.processing.dispatch_normalize_video'
        ) as mock_dispatch,
        mock.patch('anthias_server.processing.stamp_processing_start'),
        mock.patch('anthias_server.api.serializers.mixins.rename'),
        mock.patch('anthias_server.api.serializers.v1_1.rename'),
        # ``validate_uri`` raises ``Exception('Invalid file path…')``
        # when its target doesn't exist on disk. v1 / v1.1 imports it
        # from ``serializers.v1_1``; v1.2 / v2 import it via the mixin
        # — patch both binding sites.
        mock.patch(
            'anthias_server.api.serializers.mixins.validate_uri',
            return_value=True,
        ),
        mock.patch(
            'anthias_server.api.serializers.v1_1.validate_uri',
            return_value=True,
        ),
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
        mock.patch(
            'anthias_server.api.serializers.v1_1.url_fails',
            return_value=False,
        ),
        # v1.1's prepare_asset calls get_video_duration synchronously
        # when duration is 0; return a non-None timedelta so the
        # validator accepts the row.
        mock.patch(
            'anthias_server.api.serializers.v1_1.get_video_duration',
            return_value=__import__('datetime').timedelta(seconds=10),
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    asset_id = response.data['asset_id']
    mock_dispatch.assert_called_once_with(asset_id)


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1_2', 'v2'])
def test_create_heic_image_dispatches_normalize_task_on_v1_2_and_v2(
    api_client: APIClient,
    version: str,
) -> None:
    """HEIC uploads route through ``normalize_image_asset`` on v1.2 / v2
    only — image normalisation deliberately stays opt-in for the older
    endpoints (see ``CreateAssetSerializerV1_1._pending_normalize``
    docstring for the rationale). Same dispatch-gap regression guard as
    the video test but scoped to the versions where image normalize is
    wired."""
    local_heic_uri = '/data/anthias_assets/test-image.heic'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': local_heic_uri,
        'mimetype': 'image',
        # The mixin's rename uses ``ext`` on the destination filename;
        # without it, the file lands extensionless and
        # ``needs_image_normalisation`` returns False → no dispatch.
        'ext': '.heic',
    }
    asset_list_url = reverse(f'api:asset_list_{version}')

    with (
        mock.patch(
            'anthias_server.processing.dispatch_normalize_image'
        ) as mock_dispatch,
        mock.patch('anthias_server.processing.stamp_processing_start'),
        mock.patch('anthias_server.api.serializers.mixins.rename'),
        mock.patch(
            'anthias_server.api.serializers.mixins.validate_uri',
            return_value=True,
        ),
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    asset_id = response.data['asset_id']
    mock_dispatch.assert_called_once_with(asset_id)


# ---------------------------------------------------------------------------
# Remote video URL auto-download (issue #2894)
# ---------------------------------------------------------------------------
#
# Mirror of the YouTube dispatch tests above. v1.2 / v2 are the only
# versions that detect http(s) video URLs and rewrite them into a
# local-download row; v1 / v1.1 keep literal-URL semantics by design.


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1_2', 'v2'])
def test_create_remote_video_url_dispatches_download_task(
    api_client: APIClient, version: str
) -> None:
    """POSTing a http(s) URL pointing at a single-file video container
    (.mp4 in this test) on v1.2 / v2 must rewrite the row to a local
    destination path, flip ``is_processing=True``, and dispatch the
    new ``download_remote_video_asset`` task with the source URL.

    This is the core acceptance criterion for issue #2894: the row's
    final state is a local download that runs through
    ``normalize_video_asset``, not a streaming URL that bypasses the
    per-board codec gate.
    """
    remote_url = 'https://example.com/test-clip.mp4'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': remote_url,
        'mimetype': 'video',
        'duration': 0,
    }
    asset_list_url = reverse(f'api:asset_list_{version}')
    dispatch_target = (
        f'anthias_server.api.views.{version}.dispatch_remote_video_download'
    )
    with (
        mock.patch(dispatch_target) as mock_dispatch,
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    asset_id = response.data['asset_id']
    # Row landed with is_processing=True (a boolean on v2, an int on
    # v1.2) and the URI rewritten to the local destination so the
    # viewer's filesystem check will pick up the file once the task
    # finishes downloading.
    assert response.data['is_processing'] in (True, 1)
    assert response.data['uri'].endswith(f'{asset_id}.mp4')
    mock_dispatch.assert_called_once_with(asset_id, remote_url)


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1'])
def test_create_remote_video_url_keeps_literal_uri_on_legacy_endpoints(
    api_client: APIClient, version: str
) -> None:
    """v1 / v1.1 deliberately do NOT auto-download remote video URLs —
    they preserve the literal-URL semantics older clients depend on,
    matching how image normalisation stayed v1.2 / v2-only. The row
    lands with the original URI intact and ``is_processing`` False.

    Guards against a future "consistency" refactor accidentally
    promoting auto-download onto the legacy surface.
    """
    remote_url = 'https://example.com/test-clip.mp4'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': remote_url,
        'mimetype': 'video',
        'duration': 0,
    }
    asset_list_url = reverse(f'api:asset_list_{version}')
    with (
        mock.patch(
            'anthias_server.api.serializers.v1_1.url_fails',
            return_value=False,
        ),
        # The v1.1 serializer probes duration synchronously when 0
        # is passed; mock that out so the test isn't dependent on an
        # ffprobe-able fixture file.
        mock.patch(
            'anthias_server.api.serializers.v1_1.get_video_duration',
            return_value=__import__('datetime').timedelta(seconds=10),
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    # Literal URI preserved; no is_processing flag.
    assert response.data['uri'] == remote_url
    assert response.data['is_processing'] in (False, 0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    'stream_uri',
    [
        # HLS manifest: extension match short-circuits to "stream".
        'https://example.com/live/stream.m3u8',
        # RTSP scheme: streaming-by-construction even if the path
        # ended in ``.mp4`` (which it doesn't here). Scheme check
        # rejects before any HEAD probe.
        'rtsp://camera.local/feed',
    ],
    ids=['hls_manifest', 'rtsp'],
)
@pytest.mark.parametrize('version', ['v1_2', 'v2'])
def test_create_stream_uri_stays_as_literal(
    api_client: APIClient, version: str, stream_uri: str
) -> None:
    """Stream URLs (HLS manifests, RTSP feeds, ...) must NOT be
    auto-downloaded — they describe a live stream rather than a single
    file. The serializer's classify routes both shapes through as
    literal URIs and the viewer plays them live.

    Out-of-scope per the issue, but enshrining it as a test keeps a
    future classifier change from silently breaking the stream
    playback path.
    """
    payload = {
        **ASSET_CREATION_DATA,
        'uri': stream_uri,
        'mimetype': 'video',
        'duration': 0,
    }
    asset_list_url = reverse(f'api:asset_list_{version}')
    dispatch_target = (
        f'anthias_server.api.views.{version}.dispatch_remote_video_download'
    )
    with (
        mock.patch(dispatch_target) as mock_dispatch,
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
        # The mixin's stream-URL branch falls through to an inline
        # ``get_video_duration`` ffprobe; the test must not actually
        # shell out to ffprobe against the test URLs.
        mock.patch(
            'anthias_server.api.serializers.mixins.get_video_duration',
            return_value=__import__('datetime').timedelta(seconds=10),
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    # Stream URI preserved verbatim; no download dispatch.
    assert response.data['uri'] == stream_uri
    assert response.data['is_processing'] in (False, 0)
    mock_dispatch.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize('version', ['v1', 'v1_1'])
def test_create_heic_image_does_not_dispatch_on_legacy_endpoints(
    api_client: APIClient,
    version: str,
) -> None:
    """v1 / v1.1 deliberately do NOT route HEIC uploads through the
    image-normalize pipeline. Image normalisation is opt-in via v1.2 /
    v2 — promoting it on the legacy endpoints would change synchronous
    availability semantics for older clients (see
    ``CreateAssetSerializerV1_1._pending_normalize`` docstring).

    The flip side of GH #2870: video normalisation IS dispatched on
    v1 / v1.1 (the bug being fixed), but image normalisation stays
    where it was. This test guards against a future "consistency"
    refactor accidentally promoting image dispatch onto the legacy
    surface."""
    local_heic_uri = '/data/anthias_assets/test-image.heic'
    payload = {
        **ASSET_CREATION_DATA,
        'uri': local_heic_uri,
        'mimetype': 'image',
    }
    asset_list_url = reverse(f'api:asset_list_{version}')

    with (
        mock.patch(
            'anthias_server.processing.dispatch_normalize_image'
        ) as mock_dispatch,
        mock.patch('anthias_server.api.serializers.v1_1.rename'),
        # ``validate_uri`` rejects non-existent local paths; this
        # short-circuits the disk-existence check the same way the
        # video test above does.
        mock.patch(
            'anthias_server.api.serializers.v1_1.validate_uri',
            return_value=True,
        ),
        mock.patch(
            'anthias_server.api.serializers.v1_1.url_fails',
            return_value=False,
        ),
    ):
        response = api_client.post(
            asset_list_url, data=get_request_data(payload, version)
        )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    mock_dispatch.assert_not_called()
