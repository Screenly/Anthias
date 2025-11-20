import logging
from os import path
from random import shuffle

from django.utils import timezone

from anthias_app.models import Asset
from settings import settings


def get_specific_asset(asset_id):
    logging.info('Getting specific asset')
    try:
        return Asset.objects.get(asset_id=asset_id).__dict__
    except Asset.DoesNotExist:
        logging.debug('Asset %s not found in database', asset_id)
        return None


def generate_asset_list():
    """Choose deadline via:
    1. Map assets to deadlines with rule: if asset is active then
       'end_date' else 'start_date'
    2. Get nearest deadline
    """
    logging.info('Generating asset-list...')
    assets = Asset.objects.all()
    deadlines = [
        asset.end_date if asset.is_active() else asset.start_date
        for asset in assets
    ]

    enabled_assets = Asset.objects.filter(
        is_enabled=True,
        start_date__isnull=False,
        end_date__isnull=False,
    ).order_by('play_order')
    playlist = [
        {k: v for k, v in asset.__dict__.items() if k not in ['_state', 'md5']}
        for asset in enabled_assets
        if asset.is_active()
    ]

    deadline = sorted(deadlines)[0] if len(deadlines) > 0 else None
    logging.debug('generate_asset_list deadline: %s', deadline)

    if settings['shuffle_playlist']:
        shuffle(playlist)

    return playlist, deadline


class Scheduler(object):
    def __init__(self, *args, **kwargs):
        logging.debug('Scheduler init')
        self.assets = []
        self.counter = 0
        self.current_asset_id = None
        self.deadline = None
        self.extra_asset = None
        self.index = 0
        self.reverse = 0
        self.update_playlist()

    def get_next_asset(self):
        logging.debug('get_next_asset')

        if self.extra_asset is not None:
            asset = get_specific_asset(self.extra_asset)
            if asset and asset['is_processing']:
                self.current_asset_id = self.extra_asset
                self.extra_asset = None
                return asset
            logging.error('Asset not found or processed')
            self.extra_asset = None

        self.refresh_playlist()
        logging.debug('get_next_asset after refresh')
        if not self.assets:
            self.current_asset_id = None
            return None
        if self.reverse:
            idx = (self.index - 2) % len(self.assets)
            self.index = (self.index - 1) % len(self.assets)
            self.reverse = False
        else:
            idx = self.index
            self.index = (self.index + 1) % len(self.assets)

        logging.debug(
            'get_next_asset counter %s returning asset %s of %s',
            self.counter,
            idx + 1,
            len(self.assets),
        )

        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1

        current_asset = self.assets[idx]
        self.current_asset_id = current_asset.get('asset_id')
        return current_asset

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = timezone.now()

        logging.debug(
            'refresh: counter: (%s) deadline (%s) timecur (%s)',
            self.counter,
            self.deadline,
            time_cur,
        )

        if self.get_db_mtime() > self.last_update_db_mtime:
            logging.debug('updating playlist due to database modification')
            self.update_playlist()
        elif settings['shuffle_playlist'] and self.counter >= 5:
            self.update_playlist()
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def update_playlist(self):
        logging.debug('update_playlist')
        self.last_update_db_mtime = self.get_db_mtime()
        (new_assets, new_deadline) = generate_asset_list()
        if new_assets == self.assets and new_deadline == self.deadline:
            # If nothing changed, don't disturb the current play-through.
            return

        self.assets, self.deadline = new_assets, new_deadline
        self.counter = 0
        # Try to keep the same position in the play list. E.g., if a new asset
        # is added to the end of the list, we don't want to start over from
        # the beginning.
        self.index = self.index % len(self.assets) if self.assets else 0
        logging.debug(
            'update_playlist done, count %s, counter %s, index %s, deadline %s',  # noqa: E501
            len(self.assets),
            self.counter,
            self.index,
            self.deadline,
        )

    def get_db_mtime(self):
        # get database file last modification time
        try:
            return path.getmtime(settings['database'])
        except (OSError, TypeError):
            return 0
