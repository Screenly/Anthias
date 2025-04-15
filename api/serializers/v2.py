from django.utils import timezone
from rest_framework.serializers import (
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    ModelSerializer,
    Serializer,
)

from anthias_app.models import Asset
from api.serializers import UpdateAssetSerializer
from api.serializers.mixins import CreateAssetSerializerMixin


class AssetSerializerV2(ModelSerializer, CreateAssetSerializerMixin):
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
