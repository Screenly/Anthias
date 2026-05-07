from datetime import timezone
from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.serializers import (
    BooleanField,
    CharField,
    ChoiceField,
    DateTimeField,
    IntegerField,
    ListField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
    TimeField,
)

from anthias_server.app.models import Asset, REFRESH_INTERVAL_S_MAX
from anthias_server.api.serializers import UpdateAssetSerializer
from anthias_server.api.serializers.mixins import CreateAssetSerializerMixin


def _normalise_play_days(value: list[int]) -> list[int]:
    """Return a sorted, deduped list of weekday ints. Raises
    ValidationError for items outside [1..7] or for an empty selection.

    Empty `play_days` is rejected explicitly: silently widening it to
    "all days" would surprise an operator who unchecked everything
    expecting "never play". Disabling the asset (is_enabled=false) is
    the right primitive for that intent.

    Stays a list so DRF's ListField.to_representation can round-trip
    through serializer.data (the create view passes that dict straight
    into Asset.objects.create()). The TextField column stringifies the
    list at save time.
    """
    for d in value:
        if not isinstance(d, int) or d < 1 or d > 7:
            raise serializers.ValidationError(
                f'Invalid day: {d}. Must be 1 (Mon) - 7 (Sun).'
            )
    deduped = sorted(set(value))
    if not deduped:
        raise serializers.ValidationError(
            'play_days must contain at least one day. To stop playback '
            'entirely, disable the asset (is_enabled=false).'
        )
    return deduped


# Per-asset webpage auto-refresh cadence is stored inside ``Asset.metadata``
# but exposed as a top-level field on the v2 serializers so ``metadata``
# can stay read-only (the upload pipeline owns those keys). The cap
# itself lives on the model (REFRESH_INTERVAL_S_MAX, imported above) so
# the form handler in app/views.py and the v2 API agree on the same
# value without drift.


def _validate_time_window(
    attrs: dict[str, Any],
    instance: Asset | None = None,
) -> dict[str, Any]:
    """Both play_time_from and play_time_to must be set, or neither.

    The model treats either side being null as "no time-of-day filter",
    so a partial window would silently disable the constraint while
    the UI showed it as enabled. We check the *post-update* state so
    PATCHes that touch only one field still see the merged result.
    """

    def resolve(field: str) -> Any:
        if field in attrs:
            return attrs[field]
        if instance is not None:
            return getattr(instance, field, None)
        return None

    has_from = resolve('play_time_from') is not None
    has_to = resolve('play_time_to') is not None
    if has_from != has_to:
        raise serializers.ValidationError(
            {
                'play_time_to' if has_from else 'play_time_from': (
                    'play_time_from and play_time_to must be set together.'
                )
            }
        )
    return attrs


def _clamp_refresh_interval(value: Any) -> int:
    """Clamp ``metadata['refresh_interval_s']`` to ``[0, MAX]``.

    The serializer's write path rejects out-of-range values, but a
    hand-edited row or a legacy import could leave a junk value in
    there. Surfacing it as-is would contradict the documented
    0..REFRESH_INTERVAL_S_MAX contract and could let a client UI
    display / accept a value the next PATCH would 400 on. Used by
    both the top-level ``refresh_interval_s`` field and the
    sanitised ``metadata`` dict so the two halves of the response
    can't disagree.
    """
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(interval, REFRESH_INTERVAL_S_MAX))


class AssetSerializerV2(ModelSerializer[Asset], CreateAssetSerializerMixin):
    is_active = SerializerMethodField()
    play_days = SerializerMethodField()
    refresh_interval_s = SerializerMethodField()
    metadata = SerializerMethodField()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_active(self, obj: Asset) -> bool:
        return obj.is_active()

    @extend_schema_field({'type': 'array', 'items': {'type': 'integer'}})
    def get_play_days(self, obj: Asset) -> list[int]:
        return obj.get_play_days()

    @extend_schema_field(OpenApiTypes.INT)
    def get_refresh_interval_s(self, obj: Asset) -> int:
        # Pulled out of metadata so it shows up as a first-class column
        # on GET; the field is itself written from UpdateAssetSerializerV2
        # back into metadata. Default 0 = no auto-refresh, mirroring the
        # viewer's handling for assets without the key set.
        return _clamp_refresh_interval(
            (obj.metadata or {}).get('refresh_interval_s', 0)
        )

    @extend_schema_field({'type': 'object', 'additionalProperties': True})
    def get_metadata(self, obj: Asset) -> dict[str, Any]:
        # Sanitise ``refresh_interval_s`` in the embedded metadata too,
        # so a legacy/hand-edited row can't return a top-level
        # ``refresh_interval_s: 0`` while the ``metadata`` field still
        # echoes the raw out-of-range value. Other keys (the upload-
        # pipeline's original_ext / transcoded / error_message) pass
        # through untouched.
        raw = dict(obj.metadata or {})
        if 'refresh_interval_s' in raw:
            raw['refresh_interval_s'] = _clamp_refresh_interval(
                raw['refresh_interval_s']
            )
        return raw

    class Meta:
        model = Asset
        fields = [
            'asset_id',
            'name',
            'uri',
            'start_date',
            'end_date',
            'duration',
            'mimetype',
            'is_enabled',
            'nocache',
            'play_order',
            'skip_asset_check',
            'is_active',
            'is_processing',
            'play_days',
            'play_time_from',
            'play_time_to',
            'is_reachable',
            'last_reachability_check',
            'metadata',
            'refresh_interval_s',
        ]
        read_only_fields = [
            'is_reachable',
            'last_reachability_check',
            # ``metadata`` is owned by the upload-pipeline tasks
            # (image_normalize_asset, video_normalize_asset). Operators
            # can read the original-extension / transcoded / error
            # bookkeeping but can't overwrite it from the API — letting
            # them stomp on it would invite "transcoded=true but the
            # file is the original" desync. Same posture as
            # is_reachable / last_reachability_check above. The webpage
            # auto-refresh interval is surfaced as its own writable
            # field (refresh_interval_s) so operators can edit just
            # that one key without opening the whole bag.
            'metadata',
        ]


class CreateAssetSerializerV2(
    Serializer[dict[str, Any]], CreateAssetSerializerMixin
):
    def __init__(
        self,
        *args: Any,
        unique_name: bool = False,
        **kwargs: Any,
    ) -> None:
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    asset_id = CharField(read_only=True)
    ext = CharField(write_only=True, required=False)
    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = IntegerField()
    mimetype = CharField()
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = BooleanField(required=False)
    play_days = ListField(
        child=IntegerField(min_value=1, max_value=7),
        required=False,
    )
    play_time_from = TimeField(required=False, allow_null=True)
    play_time_to = TimeField(required=False, allow_null=True)
    # write_only because ``Asset`` has no ``refresh_interval_s`` column
    # — the value lives inside ``metadata``. Keeping it out of
    # ``serializer.data`` avoids ``Asset.objects.create(**serializer.data)``
    # crashing on an unknown kwarg in the v2 POST view; the field is
    # surfaced back on the response via ``AssetSerializerV2``'s
    # SerializerMethodField. The view applies ``validated_data['metadata']``
    # (set in ``validate()`` below) to the persisted row after create().
    refresh_interval_s = IntegerField(
        required=False,
        write_only=True,
        min_value=0,
        max_value=REFRESH_INTERVAL_S_MAX,
    )

    def validate_play_days(self, value: Any) -> list[int]:
        return _normalise_play_days(value)

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        _validate_time_window(data)
        prepared = self.prepare_asset(data, version='v2')
        # POST round-trip for the webpage auto-refresh interval. Land it
        # in ``metadata`` so a fresh row that gets created with a
        # refresh interval doesn't need a follow-up PATCH to take
        # effect. Skipping the key entirely (rather than storing 0)
        # keeps ``metadata`` a clean ``{}`` for assets that didn't ask
        # for auto-refresh, matching what the upload pipeline expects.
        # ``metadata`` is not a declared field on this serializer, so
        # it appears in ``validated_data`` but not in ``serializer.data``
        # — the v2 POST view reads it from ``validated_data`` and
        # applies it to the asset after Asset.objects.create().
        if 'refresh_interval_s' in data:
            metadata = dict(prepared.get('metadata') or {})
            metadata['refresh_interval_s'] = int(data['refresh_interval_s'])
            prepared['metadata'] = metadata
        return prepared


class UpdateAssetSerializerV2(UpdateAssetSerializer):
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    skip_asset_check = BooleanField(required=False)
    duration = IntegerField()
    play_days = ListField(
        child=IntegerField(min_value=1, max_value=7),
        required=False,
    )
    play_time_from = TimeField(required=False, allow_null=True)
    play_time_to = TimeField(required=False, allow_null=True)
    refresh_interval_s = IntegerField(
        required=False,
        min_value=0,
        max_value=REFRESH_INTERVAL_S_MAX,
    )

    def validate_play_days(self, value: Any) -> list[int]:
        return _normalise_play_days(value)

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return _validate_time_window(data, instance=self.instance)

    def update(self, instance: Asset, validated_data: dict[str, Any]) -> Asset:
        # Apply schedule fields before delegating: super().update() calls
        # instance.save() at the end, so this lands in a single write.
        for field in ('play_days', 'play_time_from', 'play_time_to'):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        if 'refresh_interval_s' in validated_data:
            # Merge into metadata so pipeline-owned keys (original_ext,
            # transcoded, error_message) survive the update — clobbering
            # them via dict assignment would resurrect the
            # "transcoded=true but file is original" desync we made
            # ``metadata`` read-only to prevent. For non-webpage assets
            # we accept and persist the value but it's a no-op at
            # playback time (viewer only branches on refresh_interval_s
            # for ``mimetype contains 'web'``).
            metadata = dict(instance.metadata or {})
            metadata['refresh_interval_s'] = int(
                validated_data['refresh_interval_s']
            )
            instance.metadata = metadata
        return super().update(instance, validated_data)


class DeviceSettingsSerializerV2(Serializer[Any]):
    player_name = CharField()
    audio_output = CharField()
    default_duration = IntegerField()
    default_streaming_duration = IntegerField()
    date_format = CharField()
    auth_backend = CharField()
    show_splash = BooleanField()
    default_assets = BooleanField()
    shuffle_playlist = BooleanField()
    use_24_hour_clock = BooleanField()
    debug_logging = BooleanField()
    username = CharField()


class UpdateDeviceSettingsSerializerV2(Serializer[Any]):
    player_name = CharField(required=False, allow_blank=True)
    audio_output = CharField(required=False)
    default_duration = IntegerField(required=False)
    default_streaming_duration = IntegerField(required=False)
    date_format = CharField(required=False)
    show_splash = BooleanField(required=False)
    default_assets = BooleanField(required=False)
    shuffle_playlist = BooleanField(required=False)
    use_24_hour_clock = BooleanField(required=False)
    debug_logging = BooleanField(required=False)
    username = CharField(required=False, allow_blank=True)
    password = CharField(required=False, allow_blank=True)
    password_2 = CharField(required=False, allow_blank=True)
    auth_backend = ChoiceField(
        required=False,
        allow_blank=True,
        choices=[
            ('', 'No authentication'),
            ('auth_basic', 'Basic authentication'),
        ],
    )
    current_password = CharField(required=False, allow_blank=True)


class IntegrationsSerializerV2(Serializer[Any]):
    is_balena = BooleanField()
    balena_device_id = CharField(required=False, allow_null=True)
    balena_app_id = CharField(required=False, allow_null=True)
    balena_app_name = CharField(required=False, allow_null=True)
    balena_supervisor_version = CharField(required=False, allow_null=True)
    balena_host_os_version = CharField(required=False, allow_null=True)
    balena_device_name_at_init = CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
