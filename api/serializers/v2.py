from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework.serializers import (
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
)

from anthias_app.models import Asset
from api.serializers import UpdateAssetSerializer
from api.serializers.mixins import CreateAssetSerializerMixin


class AssetSerializerV2(ModelSerializer, CreateAssetSerializerMixin):
    is_active = SerializerMethodField()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_active(self, obj):
        return obj.is_active()

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

    def validate(self, data):
        return self.prepare_asset(data, version='v2')


class UpdateAssetSerializerV2(UpdateAssetSerializer):
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    skip_asset_check = BooleanField(required=False)
    duration = IntegerField()


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


class UpdateDeviceSettingsSerializerV2(Serializer):
    player_name = CharField(required=False)
    audio_output = CharField(required=False)
    default_duration = IntegerField(required=False)
    default_streaming_duration = IntegerField(required=False)
    date_format = CharField(required=False)
    show_splash = BooleanField(required=False)
    default_assets = BooleanField(required=False)
    shuffle_playlist = BooleanField(required=False)
    use_24_hour_clock = BooleanField(required=False)
    debug_logging = BooleanField(required=False)


class IntegrationsSerializerV2(Serializer):
    is_balena = BooleanField()
    balena_device_id = CharField(required=False)
    balena_app_id = CharField(required=False)
    balena_app_name = CharField(required=False)
    balena_supervisor_version = CharField(required=False)
    balena_host_os_version = CharField(required=False)
    balena_device_name_at_init = CharField(required=False)
