import logging
import secrets
from datetime import datetime, timedelta
from os import path
from typing import Any

from django.utils import timezone

from anthias_app.models import Asset
from settings import settings

# Re-evaluate windowed playlists at most this often. Day-of-week and
# time-of-day boundaries don't show up in start_date/end_date, so we
# need a polling cap to ensure transitions are picked up.
WINDOWED_DEADLINE_CAP_SECONDS = 60

_sysrandom = secrets.SystemRandom()


def get_specific_asset(asset_id: str) -> dict[str, Any] | None:
    logging.info('Getting specific asset')
    try:
        result: dict[str, Any] = Asset.objects.get(asset_id=asset_id).__dict__
        return result
    except Asset.DoesNotExist:
        logging.debug('Asset %s not found in database', asset_id)
        return None


def _asset_to_dict(asset: Asset) -> dict[str, Any]:
    return {
        k: v for k, v in asset.__dict__.items() if k not in ['_state', 'md5']
    }


def generate_asset_list() -> tuple[list[dict[str, Any]], datetime | None]:
    """Build the playlist plus a deadline for the next re-evaluation.

    Active assets are filtered by Asset.is_active() (which now applies
    day-of-week and time-of-day windows on top of the existing date
    range and is_enabled checks). is_active() is evaluated once per
    asset against a shared `now` so the playlist filter and deadline
    computation always agree on activeness.

    Deadline is the soonest of:
      - any inactive asset's start_date,
      - any active asset's end_date,
      - now + WINDOWED_DEADLINE_CAP_SECONDS, if any asset has a window
        filter (those transitions don't show up in date columns).
    """
    logging.info('Generating asset-list...')
    now = timezone.now()

    candidates = list(
        Asset.objects.filter(
            is_enabled=True,
            start_date__isnull=False,
            end_date__isnull=False,
        ).order_by('play_order')
    )

    active_flags = [a.is_active(now=now) for a in candidates]
    playlist = [
        _asset_to_dict(a) for a, ok in zip(candidates, active_flags) if ok
    ]

    if settings['shuffle_playlist']:
        _sysrandom.shuffle(playlist)

    deadline = _compute_deadline(candidates, active_flags, now)
    logging.debug(
        'generate_asset_list: %d assets, deadline %s',
        len(playlist),
        deadline,
    )
    return playlist, deadline


def _compute_deadline(
    assets: list[Asset],
    active_flags: list[bool],
    now: datetime,
) -> datetime | None:
    """Soonest future moment when the playlist might need re-evaluating.

    Past boundaries are dropped so a long-ago start_date on an asset
    that's currently inactive (e.g. blocked by its play_days filter)
    doesn't pin the deadline to "always overdue" and cause
    refresh_playlist() to fire on every tick.
    """
    candidates: list[datetime] = []
    has_windowed = False

    for asset, is_active in zip(assets, active_flags):
        boundary = asset.end_date if is_active else asset.start_date
        if boundary and boundary > now:
            candidates.append(boundary)
        if asset.has_window_filter():
            has_windowed = True

    if has_windowed:
        candidates.append(
            now + timedelta(seconds=WINDOWED_DEADLINE_CAP_SECONDS)
        )

    return min(candidates) if candidates else None


class Scheduler:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        logging.debug('Scheduler init')
        self.assets: list[dict[str, Any]] = []
        self.counter: int = 0
        self.current_asset_id: str | None = None
        self.deadline: datetime | None = None
        self.extra_asset: str | None = None
        self.index: int = 0
        self.reverse: bool = False
        self.last_update_db_mtime: float = 0
        self.update_playlist()

    def get_next_asset(self) -> dict[str, Any] | None:
        logging.debug('get_next_asset')

        if self.extra_asset is not None:
            asset = get_specific_asset(self.extra_asset)
            if asset and not asset['is_processing']:
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

    def refresh_playlist(self) -> None:
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
            # End-of-cycle reshuffle: the current play-through is over,
            # so it's safe to take the freshly shuffled order.
            self.update_playlist(allow_reshuffle=True)
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def update_playlist(self, *, allow_reshuffle: bool = False) -> None:
        logging.debug('update_playlist')
        self.last_update_db_mtime = self.get_db_mtime()
        (new_assets, new_deadline) = generate_asset_list()

        if settings['shuffle_playlist'] and not allow_reshuffle:
            # generate_asset_list() reshuffles on every call, so list
            # equality would always fail and disrupt the play-through
            # whenever the cap-driven refresh fires (~60s for windowed
            # assets). Compare by membership only here; legitimate
            # reshuffles (end-of-cycle, counter >= 5) opt in via
            # allow_reshuffle.
            current_ids = sorted(a['asset_id'] for a in self.assets)
            new_ids = sorted(a['asset_id'] for a in new_assets)
            if current_ids == new_ids:
                # Membership unchanged: preserve current order, but
                # refresh each dict so DB-driven field edits (duration,
                # uri, etc.) take effect on the next get_next_asset().
                new_by_id = {a['asset_id']: a for a in new_assets}
                self.assets = [new_by_id[a['asset_id']] for a in self.assets]
                self.deadline = new_deadline
                return
        elif new_assets == self.assets and new_deadline == self.deadline:
            # Shuffle off: list equality is meaningful, so a no-op
            # refresh shouldn't disturb the current play-through.
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

    def get_db_mtime(self) -> float:
        # get database file last modification time
        try:
            return path.getmtime(settings['database'])
        except (OSError, TypeError):
            return 0
