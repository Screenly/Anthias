import uuid
from os import path, rename

from lib.utils import (
    download_video_from_youtube,
    get_video_duration,
    url_fails,
)
from settings import settings

from . import (
    get_unique_name,
    validate_uri,
)


class CreateAssetSerializerMixin:
    def prepare_asset(self, data, asset_id=None, version='v2'):
        ampersand_fix = '&amp;'
        name = data['name'].replace(ampersand_fix, '&')

        if self.unique_name:
            name = get_unique_name(name)

        asset = {
            'name': name,
            'mimetype': data.get('mimetype'),
            'is_enabled': data.get(
                'is_enabled',
                False if version == 'v2' else 0
            ),
            'nocache': data.get(
                'nocache',
                False if version == 'v2' else 0
            ),
        }

        uri = (
            data['uri']
            .replace(ampersand_fix, '&')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\'', '&apos;')
            .replace('\"', '&quot;')
        )

        validate_uri(uri)

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
            asset['is_processing'] = True if version == 'v2' else 1

        asset['uri'] = uri

        if "video" in asset['mimetype']:
            if int(data.get('duration')) == 0:
                duration = get_video_duration(uri).total_seconds()
                asset['duration'] = (
                    duration if version == 'v2' else int(duration)
                )
        else:
            # Crashes if it's not an int. We want that.
            duration = data.get(
                'duration', settings['default_duration']
            )

            if version == 'v2':
                asset['duration'] = duration
            else:
                asset['duration'] = int(duration)

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
