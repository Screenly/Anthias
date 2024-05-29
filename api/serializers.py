from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    Serializer,
)

class AssetSerializer(Serializer):
    asset_id = CharField()
    name = CharField()
    uri = CharField()
    start_date = DateTimeField()
    end_date = DateTimeField()
    duration = CharField()
    mimetype = CharField()
    is_active = IntegerField(min_value=0, max_value=1)
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField()
    play_order = IntegerField()
    skip_asset_check = IntegerField(min_value=0, max_value=1)
