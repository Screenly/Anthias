from django.utils import timezone
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    Serializer,
)

from api.serializers.mixins import CreateAssetSerializerMixin


class CreateAssetSerializerV1_2(Serializer, CreateAssetSerializerMixin):
    def __init__(self, *args, unique_name=False, **kwargs):
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    asset_id = CharField(read_only=True)
    ext = CharField(write_only=True, required=False)
    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    mimetype = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1, required=False)
    nocache = IntegerField(min_value=0, max_value=1, required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    def validate(self, data):
        return self.prepare_asset(data, version='v1_2')
