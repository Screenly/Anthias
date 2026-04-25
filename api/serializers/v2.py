import json

from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
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

from anthias_app.models import Asset
from api.serializers import UpdateAssetSerializer
from api.serializers.mixins import CreateAssetSerializerMixin


def _normalise_play_days(value):
    """Coerce a list-or-JSON-string of weekdays into a sorted, deduped
    JSON string ready for the TextField column. Raises ValidationError
    for shapes outside [1..7] or for an empty selection.

    Empty `play_days` is rejected explicitly: silently widening it to
    "all days" would surprise an operator who unchecked everything
    expecting "never play". Disabling the asset (is_enabled=false) is
    the right primitive for that intent.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise serializers.ValidationError(
                'play_days must be a JSON array of integers 1-7.'
            )
    if not isinstance(value, list):
        raise serializers.ValidationError('play_days must be a list.')
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
    return json.dumps(deduped)


def _validate_time_window(attrs, instance=None):
    """Both play_time_from and play_time_to must be set, or neither.

    The model treats either side being null as "no time-of-day filter",
    so a partial window would silently disable the constraint while
    the UI showed it as enabled. We check the *post-update* state so
    PATCHes that touch only one field still see the merged result.
    """

    def resolve(field):
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


class AssetSerializerV2(ModelSerializer, CreateAssetSerializerMixin):
    is_active = SerializerMethodField()
    play_days = SerializerMethodField()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_active(self, obj):
        return obj.is_active()

    @extend_schema_field({'type': 'array', 'items': {'type': 'integer'}})
    def get_play_days(self, obj):
        return obj.get_play_days()

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
        ]


class CreateAssetSerializerV2(Serializer, CreateAssetSerializerMixin):
    def __init__(self, *args, unique_name=False, **kwargs):
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

    def validate_play_days(self, value):
        return _normalise_play_days(value)

    def validate(self, data):
        _validate_time_window(data)
        return self.prepare_asset(data, version='v2')


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

    def validate_play_days(self, value):
        return _normalise_play_days(value)

    def validate(self, data):
        return _validate_time_window(data, instance=self.instance)

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        for field in ('play_days', 'play_time_from', 'play_time_to'):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance


class DeviceSettingsSerializerV2(Serializer):
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


class UpdateDeviceSettingsSerializerV2(Serializer):
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


class IntegrationsSerializerV2(Serializer):
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
