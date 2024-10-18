import uuid
from os import path, rename
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
    duration = IntegerField()
    mimetype = CharField()
    is_enabled = BooleanField()
    nocache = BooleanField()
    play_order = IntegerField()
    skip_asset_check = BooleanField()


class AssetSerializer(ModelSerializer):
    is_active = BooleanField()

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


class CreateAssetSerializerV1_1(Serializer):
    def __init__(self, *args, unique_name=False, **kwargs):
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc, required=False)
    end_date = DateTimeField(default_timezone=timezone.utc, required=False)
    duration = IntegerField(required=False)
    mimetype = CharField()
    is_enabled = BooleanField(required=False)
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = BooleanField(required=False)

    def validate(self, data):
        name = data['name']

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
            'asset_id': data.get('asset_id'),
            'is_enabled': data.get('is_enabled', False),
            'is_processing': data.get('is_processing', False),
            'nocache': data.get('nocache', False),
        }

        uri = data.get('uri')

        if uri.startswith('/'):
            if not path.isfile(uri):
                raise Exception("Invalid file path. Failed to add asset.")
        else:
            if not validate_url(uri):
                raise Exception("Invalid URL. Failed to add asset.")

        if not asset['asset_id']:
            asset['asset_id'] = uuid.uuid4().hex
            if uri.startswith('/'):
                rename(uri, path.join(settings['assetdir'], asset['asset_id']))
                uri = path.join(settings['assetdir'], asset['asset_id'])

        if 'youtube_asset' in asset['mimetype']:
            (
                uri, asset['name'], asset['duration']
            ) = download_video_from_youtube(uri, asset['asset_id'])
            asset['mimetype'] = 'video'
            asset['is_processing'] = 1

        asset['uri'] = uri

        if "video" in asset['mimetype']:
            if data.get('duration') == 0:
                asset['duration'] = int(
                    get_video_duration(uri).total_seconds())
        else:
            # Crashes if it's not an int. We want that.
            asset['duration'] = data.get('duration')

        asset['skip_asset_check'] = data.get('skip_asset_check', False)

        if data.get('start_date'):
            asset['start_date'] = data.get('start_date').replace(tzinfo=None)
        else:
            asset['start_date'] = ""

        if data.get('end_date'):
            asset['end_date'] = data.get('end_date').replace(tzinfo=None)
        else:
            asset['end_date'] = ""

        if not asset['skip_asset_check'] and url_fails(asset['uri']):
            raise Exception("Could not retrieve file. Check the asset URL.")

        return asset


class CreateAssetSerializerV1_2(Serializer):
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

    def prepare_asset(self, data, asset_id=None):
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
            'is_enabled': data.get('is_enabled', False),
            'nocache': data.get('nocache', False),
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
            asset['is_processing'] = True

        asset['uri'] = uri

        if "video" in asset['mimetype']:
            if data.get('duration') == 0:
                asset['duration'] = int(
                    get_video_duration(uri).total_seconds())
        elif data.get('duration'):
            # Crashes if it's not an int. We want that.
            asset['duration'] = data.get('duration')
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
        return self.prepare_asset(data)


class UpdateAssetSerializer(Serializer):
    name = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = IntegerField()
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = BooleanField(required=False)

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
