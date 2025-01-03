from os import path

from django.utils import timezone
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    ModelSerializer,
    Serializer,
)

from anthias_app.models import Asset
from lib.utils import validate_url


def get_unique_name(name):
    names = Asset.objects.values_list('name', flat=True)
    if name in names:
        i = 1
        while True:
            new_name = f'{name}-{i}'
            if new_name in names:
                i += 1
            else:
                return new_name

    return name


def validate_uri(uri):
    if uri.startswith('/'):
        if not path.isfile(uri):
            raise Exception("Invalid file path. Failed to add asset.")
    else:
        if not validate_url(uri):
            raise Exception("Invalid URL. Failed to add asset.")


class AssetSerializer(ModelSerializer):
    duration = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_active = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField(min_value=0, max_value=1)
    skip_asset_check = IntegerField(min_value=0, max_value=1)

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


class UpdateAssetSerializer(Serializer):
    name = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1, required=False)
    nocache = IntegerField(min_value=0, max_value=1, required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    def update(self, instance, validated_data):
        instance.name = validated_data.get('name', instance.name)
        instance.start_date = validated_data.get(
            'start_date', instance.start_date)
        instance.end_date = validated_data.get('end_date', instance.end_date)
        instance.is_enabled = validated_data.get(
            'is_enabled', instance.is_enabled)
        instance.is_processing = validated_data.get(
            'is_processing', instance.is_processing)
        instance.nocache = validated_data.get('nocache', instance.nocache)
        instance.play_order = validated_data.get(
            'play_order', instance.play_order)
        instance.skip_asset_check = validated_data.get(
            'skip_asset_check', instance.skip_asset_check)

        if 'video' not in instance.mimetype:
            instance.duration = validated_data.get(
                'duration', instance.duration)

        instance.save()

        return instance
