import uuid
from os import path, rename
from django.utils import timezone
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    ModelSerializer,
    Serializer,
)
from anthias_app.models import Asset
from lib.utils import (
    download_video_from_youtube,
    get_video_duration,
    validate_url,
    url_fails,
)
from settings import settings


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


class AssetSerializer(ModelSerializer):
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


class CreateAssetSerializer(Serializer):
    def __init__(self, *args, version='v1.2', unique_name=False, **kwargs):
        self.version = version
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    mimetype = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    # @TODO: Move this method as a standalone function.
    def prepare_asset_v1_2(self, data, asset_id=None):
        ampersand_fix = '&amp;'
        name = data['name'].replace(ampersand_fix, '&')

        if self.unique_name:
            names = Asset.objects.values_list('name', flat=True)
            if name in names:
                i = 1
                while True:
                    new_name = f'{name}-{i}'
                    if new_name in names:
                        i += 1
                    else:
                        name = new_name
                        break

        asset = {
            'name': name,
            'mimetype': data.get('mimetype'),
            'is_enabled': data.get('is_enabled'),
            'nocache': data.get('nocache'),
        }

        uri = (
            data['uri']
            .replace(ampersand_fix, '&')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\'', '&apos;')
            .replace('\"', '&quot;')
        )

        if uri.startswith('/'):
            if not path.isfile(uri):
                raise Exception("Invalid file path. Failed to add asset.")
        else:
            if not validate_url(uri):
                raise Exception("Invalid URL. Failed to add asset.")

        if not asset_id:
            asset['asset_id'] = uuid.uuid4().hex

        if not asset_id and uri.startswith('/'):
            path_name = path.join(settings['assetdir'], asset['asset_id'])
            ext_name = data.get('ext', '')
            new_uri = f'{path_name}{ext_name}'
            rename(uri, new_uri)
            uri = new_uri

        if 'youtube_asset' in asset['mimetype']:
            (
                uri, asset['name'], asset['duration']
            ) = download_video_from_youtube(uri, asset['asset_id'])
            asset['mimetype'] = 'video'
            asset['is_processing'] = 1

        asset['uri'] = uri

        if "video" in asset['mimetype']:
            if data.get('duration') == 'N/A' or int(data.get('duration')) == 0:
                asset['duration'] = int(
                    get_video_duration(uri).total_seconds())
        elif data.get('duration'):
            # Crashes if it's not an int. We want that.
            asset['duration'] = int(data.get('duration'))
        else:
            asset['duration'] = 10

        asset['play_order'] = (
            data.get('play_order') if data.get('play_order') else 0
        )

        asset['skip_asset_check'] = (
            int(data.get('skip_asset_check'))
            if int(data.get('skip_asset_check'))
            else 0
        )

        asset['start_date'] = data.get('start_date').replace(tzinfo=None)
        asset['end_date'] = data.get('end_date').replace(tzinfo=None)

        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")

        return asset

    def validate(self, data):
        if self.version == 'v1.2':
            return self.prepare_asset_v1_2(data)

        return {}


class UpdateAssetSerializer(Serializer):
    name = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = IntegerField(min_value=0, max_value=1, required=False)

    def update(self, instance, validated_data):
        instance.name = validated_data.get('name', instance.name)
        instance.start_date = validated_data.get(
            'start_date', instance.start_date)
        instance.end_date = validated_data.get('end_date', instance.end_date)
        instance.is_enabled = validated_data.get(
            'is_enabled', instance.is_enabled)
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
