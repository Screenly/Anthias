from django.utils import timezone
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    Serializer,
)


class AssetRequestSerializer(Serializer):
    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    mimetype = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField()
    play_order = IntegerField()
    skip_asset_check = IntegerField(min_value=0, max_value=1)

class AssetSerializer(AssetRequestSerializer):
    asset_id = CharField()
    is_active = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1)
