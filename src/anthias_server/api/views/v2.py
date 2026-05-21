import ipaddress
import json
import logging
import secrets
from datetime import datetime, timedelta
from os import getenv, statvfs
from platform import machine
from typing import Any

import psutil
import redis
import requests
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_server.app.helpers import (
    add_default_assets,
    remove_default_assets,
)
from anthias_server.app.models import Asset
from anthias_server.api.helpers import (
    AssetCreationError,
    finalize_asset_update,
    get_active_asset_ids,
    save_active_assets_ordering,
)
from anthias_server.lib.auth import (
    apply_auth_settings,
    operator_username,
)
from anthias_common.internal_auth import is_internal_request
from anthias_common.remote_video import dispatch_remote_video_download
from anthias_common.youtube import dispatch_download
from anthias_server.processing import dispatch_pending_normalize
from anthias_server.api.serializers.v2 import (
    AssetSerializerV2,
    CreateAssetSerializerV2,
    DeviceSettingsSerializerV2,
    IntegrationsSerializerV2,
    ScreenlyMigrateAssetSerializerV2,
    ScreenlyTokenSerializerV2,
    UpdateAssetSerializerV2,
    UpdateDeviceSettingsSerializerV2,
    ViewerPlaylistSerializerV2,
    ViewerSettingsSerializerV2,
)
from anthias_server.lib.screenly_migration import (
    MIGRATION_ASSET_GROUP_TITLE,
    ScreenlyMigrationError,
    ensure_asset_group,
    migrate_asset,
    validate_token,
)
from anthias_server.api.views.mixins import (
    AssetContentViewMixin,
    AssetsControlViewMixin,
    BackupViewMixin,
    DeleteAssetViewMixin,
    DisplayPowerViewMixin,
    FileAssetViewMixin,
    InfoViewMixin,
    PlaylistOrderViewMixin,
    RebootViewMixin,
    RecoverViewMixin,
    ShutdownViewMixin,
)
from anthias_common import device_helper
from anthias_server.lib import diagnostics
from anthias_server.lib.auth import authorized
from anthias_server.lib.github import is_up_to_date
from anthias_common.utils import (
    clamp_screen_rotation,
    connect_to_redis,
    get_balena_device_info,
    get_node_ip,
    get_node_mac_address,
    is_balena_app,
)
from anthias_server.settings import ViewerPublisher, settings

r = connect_to_redis()
logger = logging.getLogger(__name__)


# Single canonical operator-facing message for any failure to reach
# Screenly's API. Surfaced from the v2 integration endpoints in place
# of the raw RequestException text so the response body never carries
# transport-level state (resolved IPs, errno strings, proxy detail)
# — see CodeQL's information-exposure rule.
_SCREENLY_NETWORK_USER_MESSAGE = (
    "Could not reach Screenly. Check this device's internet "
    'connection and try again.'
)


# Bounded HTTP timeout for the Balena supervisor lookup below. Hit by
# every poll on Balena devices, so it must stay well under the JS
# poll cadence (2s) to keep request workers free. A real supervisor
# answers in well under this; the timeout is the worst-case bound.
_BALENA_SUPERVISOR_TIMEOUT_S = 1.5

# Debounce key + window for the bare-metal cache-miss refresh. The
# splash polls every 2s; host_agent.set_ip_addresses runs an internet
# probe with a 10x1s tenacity retry, so worst-case it takes ~10s. The
# TTL covers that window with a small margin so we don't queue
# redundant refresh requests while the first one is in flight.
_IP_REFRESH_PENDING_KEY = 'splash:ip_refresh_pending'
_IP_REFRESH_DEBOUNCE_S = 12


def _resolve_node_ip() -> str:
    """Non-blocking IP-string resolver for the splash polling endpoint.

    ``anthias_common.utils.get_node_ip()`` is the right primitive for one-shot
    server-rendered surfaces like ``/api/v2/info``: it publishes
    ``set_ip_addresses`` to host_agent and waits up to ~80s
    (60s host_agent_ready + 20s ip_addresses_ready) for the result.
    That's catastrophic from inside a 2-second polling endpoint —
    request workers would queue behind a single slow first call and
    the splash would never populate.

    This resolver is the fast-path version. On bare metal it reads
    the cached ``ip_addresses`` key directly (host_agent populates it
    on demand) and fires a fire-and-forget ``hostcmd`` publish on a
    cache miss so the next poll (~2s later) finds something to read.
    On Balena it calls the supervisor directly with a tight HTTP
    timeout, since the supervisor is the only source of truth there
    and there's no host_agent cache to fall back on.

    Returns the raw whitespace-separated IP string (or empty), shaped
    to match what ``get_node_ip()`` would have returned, so the
    formatter below can stay shared with ``InfoViewV2``.
    """
    if is_balena_app():
        # Reuse the shared anthias_common.utils helper so URL construction /
        # auth / headers stay in one place (extending it to accept a
        # timeout was the smaller change). The bounded timeout is the
        # load-bearing part of this fix — without it, a slow
        # supervisor on first boot would pin a request worker for
        # longer than the JS poll cadence.
        try:
            response = get_balena_device_info(
                timeout=_BALENA_SUPERVISOR_TIMEOUT_S,
            )
        except requests.RequestException:
            return ''
        if not response.ok:
            return ''
        try:
            return str(response.json().get('ip_address') or '')
        except ValueError:
            return ''

    try:
        raw = r.get('ip_addresses')
    except redis.RedisError:
        # Redis is the cache backing this whole codepath; if it's
        # flaking (early boot, transient broker hiccup), return ''
        # instead of 500-ing the splash poll. The JS keeps polling
        # and recovers as soon as Redis comes back.
        raw = None
    if raw:
        try:
            ips = json.loads(raw)
        except (ValueError, TypeError):
            ips = None
        # Validate the decoded payload is a list of strings — host_agent
        # writes ``json.dumps([...])`` so that's the only shape we
        # expect here. A different producer (or a corrupted write)
        # could yield a string / int / dict; ``' '.join`` would either
        # crash on non-iterable or quietly join characters of a string,
        # both of which are wrong. Treat anything that doesn't match
        # ``list[str]`` as a cache miss and fall through to refresh.
        #
        # Empty list also falls through: host_agent's first run on a
        # still-coming-up network writes ``'[]'``, and we don't want
        # the splash to freeze on that — every subsequent poll would
        # short-circuit on the empty cached value otherwise.
        if (
            isinstance(ips, list)
            and ips
            and all(isinstance(ip, str) for ip in ips)
        ):
            # Cache hit. Also kick off a debounced background refresh
            # so the splash stays current if the device's IPs change
            # during its display window (DHCP renewal, link flap,
            # operator plugging in a different cable). Without this,
            # once the cache is populated the splash would freeze on
            # whatever IPs were valid at first poll. _publish_refresh
            # is bounded by the same SETNX TTL as the cache-miss path,
            # so a busy poll loop won't queue redundant refreshes.
            _publish_refresh()
            return ' '.join(ips)

    # Cache miss / empty list / malformed / Redis flake: ask
    # host_agent to populate. The next poll picks it up — we don't
    # block waiting for completion. The JS keeps polling and the
    # splash shows "Detecting network…" until host_agent fills the
    # cache.
    _publish_refresh()
    return ''


def _publish_refresh() -> None:
    """Best-effort host_agent refresh, debounced.

    At a 2s poll cadence, host_agent's set_ip_addresses takes longer
    than one poll interval (it does an internet probe with a 10x1s
    tenacity retry). Without a debounce we'd queue many redundant
    refresh requests before the first one completes. SETNX with a
    short TTL ensures only one refresh fires per window; later polls
    no-op until the TTL expires.

    Redis errors are swallowed (with the debounce key released on
    publish failure) so a transient broker hiccup doesn't 500 the
    splash poll — the JS retries on the next poll.
    """
    try:
        acquired = r.set(
            _IP_REFRESH_PENDING_KEY,
            '1',
            nx=True,
            ex=_IP_REFRESH_DEBOUNCE_S,
        )
    except redis.RedisError:
        return
    if not acquired:
        return
    try:
        r.publish('hostcmd', 'set_ip_addresses')
    except redis.RedisError:
        # Drop the debounce key so the next poll can retry —
        # otherwise we'd wait out the TTL after a transient flake
        # that didn't actually queue a refresh.
        try:
            r.delete(_IP_REFRESH_PENDING_KEY)
        except redis.RedisError:
            pass


def _format_ip_urls(node_ip: str) -> list[str]:
    """Format a whitespace-separated IP string into clickable URLs.

    Tolerates malformed input: callers can pass leftover sentinel
    strings (e.g. 'Unknown' from ``get_node_ip()`` on a slow Balena
    boot) and ``ipaddress.ip_address()`` raises on those. Skipping
    invalid tokens keeps a single garbage value from 500-ing the
    consuming view.
    """
    if node_ip in ('Unknown', 'Unable to retrieve IP.'):
        return []
    out: list[str] = []
    for ip in node_ip.split():
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        # NOSONAR (S5332): Anthias serves the admin UI on plain HTTP per
        # CLAUDE.md; TLS is opt-in via the Caddy sidecar. The splash
        # surfaces these URLs so the operator can click into the
        # device's LAN UI — they must match how the device actually
        # listens. Emitting https:// here would point at a port that
        # isn't bound on a default install.
        if isinstance(obj, ipaddress.IPv6Address):
            out.append(f'http://[{ip}]')  # NOSONAR
        else:
            out.append(f'http://{ip}')  # NOSONAR
    return out


def _safe_ip_addresses() -> list[str]:
    """Fast-path resolver+formatter for the splash polling endpoint."""
    return _format_ip_urls(_resolve_node_ip())


class NetworkIpAddressesViewV2(APIView):
    """Lightweight IP-list endpoint for the splash page to poll.

    Unauth'd because the splash page itself is unauth'd and the viewer
    isn't a credentialed client. The data here is already disclosed by
    /splash-page rendering — there's no new exposure.

    Narrow on purpose: only IPs, no diagnostics. /api/v2/info covers
    the "everything about the device" case but is auth'd and does
    heavier work (psutil, statvfs, version checks) that would compound
    on a 2-second poll. Don't bolt onto this; add a sibling endpoint
    if a different unauth'd value is ever needed.

    **KNOWN LIMITATION (deferred to broader auth work).** This GET has
    a side effect — ``_resolve_node_ip`` calls ``_publish_refresh()``
    on cache miss / hit / empty-list, which publishes ``hostcmd:
    set_ip_addresses`` to host_agent. ``host_agent.set_ip_addresses``
    in turn does an internet probe (``requests.get`` to 1.1.1.1 with
    a 10×1s tenacity retry). An unauthenticated LAN client can drive
    that side effect at the debounce-bounded rate (one publish per
    ``_IP_REFRESH_DEBOUNCE_S``).

    The mitigations already in place keep blast radius bounded:

      * SETNX-debounced publishes (only one refresh per 12s window
        regardless of poll volume),
      * the response body carries no data not already disclosed by
        the splash page itself,
      * host_agent's own retry/throttle behavior caps the
        downstream cost.

    The proper fix is a shared internal-auth gate (matching the one
    on AssetRecheckViewV2) — but the splash polling endpoint is
    consumed by the viewer's webview from the device's local
    network with no way to attach BasicAuth, so internal-auth here
    needs to be designed alongside the broader auth rework. Tracked
    in the same followup as AssetRecheckViewV2's gating.
    """

    @extend_schema(
        summary='Get device IP addresses (for splash poll)',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'ip_addresses': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                },
            },
        },
    )
    def get(self, request: Request) -> Response:
        return Response({'ip_addresses': _safe_ip_addresses()})


class AssetListViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(
        summary='List assets', responses={200: AssetSerializerV2(many=True)}
    )
    @authorized
    def get(self, request: Request) -> Response:
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create asset',
        request=CreateAssetSerializerV2,
        responses={201: AssetSerializerV2},
    )
    @authorized
    def post(self, request: Request) -> Response:
        try:
            serializer = CreateAssetSerializerV2(
                data=request.data, unique_name=True
            )

            if not serializer.is_valid():
                raise AssetCreationError(serializer.errors)
        except AssetCreationError as error:
            return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)

        active_asset_ids = get_active_asset_ids()
        asset = Asset.objects.create(**serializer.data)
        # Apply ``metadata`` (set by CreateAssetSerializerV2.validate()
        # when the operator passes ``refresh_interval_s``). It lives in
        # ``validated_data`` rather than ``serializer.data`` because
        # ``metadata`` isn't a declared field on the create serializer
        # — surfacing it as one would open the upload-pipeline-owned
        # bag (original_ext, transcoded, error_message) for arbitrary
        # client writes. Pulling it from validated_data and applying
        # post-create keeps the read-only stance on metadata while
        # still letting the dedicated refresh_interval_s knob round-
        # trip on POST.
        post_create_metadata = serializer.validated_data.get('metadata')
        if post_create_metadata:
            existing = dict(asset.metadata or {})
            existing.update(post_create_metadata)
            asset.metadata = existing
            asset.save(update_fields=['metadata'])
        asset.refresh_from_db()

        # Kick off the YouTube download out of band when the
        # serializer flagged a youtube_asset. The row is already
        # persisted with is_processing=True; the task fills in the
        # title + duration once yt-dlp finishes.
        if serializer._pending_youtube_uri:
            dispatch_download(asset.asset_id, serializer._pending_youtube_uri)

        # Generic remote video URLs get the same hand-off: the
        # serializer pointed Asset.uri at a local destination path
        # and flipped is_processing; the Celery task downloads via
        # requests and chains into normalize_video_asset. Live
        # streams (RTSP / HLS / DASH) are excluded by the
        # serializer's ``is_downloadable_remote_video`` classify.
        if serializer._pending_remote_video_uri:
            dispatch_remote_video_download(
                asset.asset_id, serializer._pending_remote_video_uri
            )

        # Normalisation pipeline: HEIC/HEIF/TIFF → WebP, exotic video
        # → H.264 MP4. Dispatched after persistence (same pattern as
        # the YouTube hop above) so the row's ``asset_id`` is already
        # written to the DB by the time the worker picks it up. The
        # row was set ``is_processing=True`` by ``prepare_asset``;
        # the task clears it once the file lands. Shared with
        # v1/v1.1/v1.2 via dispatch_pending_normalize so adding a
        # new API version can't re-open GH #2870.
        dispatch_pending_normalize(serializer, asset.asset_id)

        if asset.is_active():
            active_asset_ids.insert(asset.play_order, asset.asset_id)

        save_active_assets_ordering(active_asset_ids)
        asset.refresh_from_db()

        return Response(
            AssetSerializerV2(asset).data,
            status=status.HTTP_201_CREATED,
        )


class AssetViewV2(APIView, DeleteAssetViewMixin):
    serializer_class = AssetSerializerV2

    @extend_schema(summary='Get asset')
    @authorized
    def get(self, request: Request, asset_id: str) -> Response:
        asset = get_object_or_404(Asset, asset_id=asset_id)
        serializer = self.serializer_class(asset)
        return Response(serializer.data)

    def update(
        self,
        request: Request,
        asset_id: str,
        partial: bool = False,
    ) -> Response:
        asset = get_object_or_404(Asset, asset_id=asset_id)
        serializer = UpdateAssetSerializerV2(
            asset, data=request.data, partial=partial
        )

        if serializer.is_valid():
            serializer.save()
        else:
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        finalize_asset_update(asset)

        return Response(AssetSerializerV2(asset).data)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={200: AssetSerializerV2},
    )
    @authorized
    def patch(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=True)

    @extend_schema(
        summary='Update asset',
        request=UpdateAssetSerializerV2,
        responses={200: AssetSerializerV2},
    )
    @authorized
    def put(self, request: Request, asset_id: str) -> Response:
        return self.update(request, asset_id, partial=False)


class AssetRecheckViewV2(APIView):
    """On-demand reachability recheck, called from the viewer.

    The viewer cannot attach operator BasicAuth credentials, so this
    endpoint uses a lightweight internal token derived from the shared
    ``anthias.conf`` secret instead. The request still remains
    side-effect-only and rate-limited: it returns no asset data, queue
    churn is debounced here, and the Celery task enforces the longer
    per-asset probe cooldown.
    """

    @extend_schema(
        summary='Recheck asset reachability',
        responses={202: None, 403: None, 404: None},
    )
    def post(self, request: Request, asset_id: str) -> Response:
        if not is_internal_request(request, settings):
            return Response(status=status.HTTP_403_FORBIDDEN)

        # Existence check before locking — a 404 is more useful than
        # a silent 202 on an unknown id, and we don't want to acquire
        # a lock for an asset that doesn't exist.
        if not Asset.objects.filter(asset_id=asset_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Imported here to avoid a circular import at module load:
        # anthias_server.celery_tasks imports api.* via Django's app registry, and
        # importing it at the top of this view module pulls celery into
        # the request path on every request even when not needed.
        from anthias_server.celery_tasks import (
            ASSET_RECHECK_QUEUE_DEBOUNCE_S,
            asset_recheck_queue_key,
            revalidate_asset_url,
        )

        # Atomic per-asset queue-debounce gate. Replaces a racy check
        # on ``asset.last_reachability_check`` — the timestamp only
        # updates after the probe completes, so near-simultaneous
        # endpoint hits would all read the same stale value and each
        # enqueue a task. SETNX with a short TTL gates queue churn:
        # only the first endpoint hit in the window enqueues, the
        # rest no-op. The actual cooldown (don't probe again within
        # RECHECK_COOLDOWN_S) is enforced by a separate task-side lock
        # in ``revalidate_asset_url`` — they're separate keys with
        # different TTLs because a single shared key would block the
        # task we just enqueued from acquiring its own lock. Returns
        # 202 on the no-op path because the recheck is effectively
        # up-to-date already; the viewer doesn't need to distinguish
        # "fresh" from "skipped due to debounce".
        if not r.set(
            asset_recheck_queue_key(asset_id),
            '1',
            nx=True,
            ex=ASSET_RECHECK_QUEUE_DEBOUNCE_S,
        ):
            return Response(status=status.HTTP_202_ACCEPTED)

        revalidate_asset_url.delay(asset_id)
        return Response(status=status.HTTP_202_ACCEPTED)


class BackupViewV2(BackupViewMixin):
    pass


class RecoverViewV2(RecoverViewMixin):
    pass


class RebootViewV2(RebootViewMixin):
    pass


class ShutdownViewV2(ShutdownViewMixin):
    pass


class DisplayPowerViewV2(DisplayPowerViewMixin):
    pass


class FileAssetViewV2(FileAssetViewMixin):
    pass


class AssetContentViewV2(AssetContentViewMixin):
    pass


class PlaylistOrderViewV2(PlaylistOrderViewMixin):
    pass


class AssetsControlViewV2(AssetsControlViewMixin):
    pass


class DeviceSettingsViewV2(APIView):
    @extend_schema(
        summary='Get device settings',
        responses={200: DeviceSettingsSerializerV2},
    )
    @authorized
    def get(self, request: Request) -> Response:
        try:
            # Force reload of settings
            settings.load()
        except Exception as e:
            logging.error(f'Failed to reload settings: {str(e)}')
            # Continue with existing settings if reload fails

        return Response(
            {
                'player_name': settings['player_name'],
                'audio_output': settings['audio_output'],
                'default_duration': int(settings['default_duration']),
                'default_streaming_duration': int(
                    settings['default_streaming_duration']
                ),
                'date_format': settings['date_format'],
                'auth_backend': settings['auth_backend'],
                'show_splash': settings['show_splash'],
                'default_assets': settings['default_assets'],
                'shuffle_playlist': settings['shuffle_playlist'],
                'use_24_hour_clock': settings['use_24_hour_clock'],
                'debug_logging': settings['debug_logging'],
                # Clamp on read too — the OpenAPI schema advertises
                # an enum of {0,90,180,270}, but a hand-edited conf
                # could have a stale 45 or any other int sitting on
                # disk. Going through the shared helper guarantees
                # the API never returns a value the client wasn't
                # told to expect (Copilot review of #2882).
                'screen_rotation': clamp_screen_rotation(
                    settings['screen_rotation']
                ),
                'username': (
                    operator_username()
                    if settings['auth_backend'] == 'auth_basic'
                    else ''
                ),
            }
        )

    @extend_schema(
        summary='Update device settings',
        request=UpdateDeviceSettingsSerializerV2,
        responses={
            200: {
                'type': 'object',
                'properties': {'message': {'type': 'string'}},
            },
            400: {
                'type': 'object',
                'properties': {'error': {'type': 'string'}},
            },
        },
    )
    @authorized
    def patch(self, request: Request) -> Response:
        try:
            serializer = UpdateDeviceSettingsSerializerV2(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=400)

            data = serializer.validated_data
            settings.load()

            current_password = data.get('current_password', '')
            auth_backend = data.get('auth_backend', settings['auth_backend'])
            prev_auth_backend = settings['auth_backend']

            apply_auth_settings(
                request,
                new_auth_backend=auth_backend,
                current_pwd=current_password,
                new_username=data.get('username', ''),
                new_pwd=data.get('password', ''),
                new_pwd_confirm=data.get('password_2', ''),
                prev_auth_backend=prev_auth_backend,
            )
            settings['auth_backend'] = auth_backend

            # Update settings
            if 'player_name' in data:
                settings['player_name'] = data['player_name']
            if 'default_duration' in data:
                settings['default_duration'] = data['default_duration']
            if 'default_streaming_duration' in data:
                settings['default_streaming_duration'] = data[
                    'default_streaming_duration'
                ]
            if 'audio_output' in data:
                settings['audio_output'] = data['audio_output']
            if 'date_format' in data:
                settings['date_format'] = data['date_format']
            if 'show_splash' in data:
                settings['show_splash'] = data['show_splash']
            if 'default_assets' in data:
                if data['default_assets'] and not settings['default_assets']:
                    add_default_assets()
                elif not data['default_assets'] and settings['default_assets']:
                    remove_default_assets()
                settings['default_assets'] = data['default_assets']
            if 'shuffle_playlist' in data:
                settings['shuffle_playlist'] = data['shuffle_playlist']
            if 'use_24_hour_clock' in data:
                settings['use_24_hour_clock'] = data['use_24_hour_clock']
            if 'debug_logging' in data:
                settings['debug_logging'] = data['debug_logging']
            if 'screen_rotation' in data:
                settings['screen_rotation'] = int(data['screen_rotation'])

            settings.save()
            publisher = ViewerPublisher.get_instance()
            publisher.send_to_viewer('reload')

            return Response({'message': 'Settings were successfully saved.'})
        except Exception:
            logger.exception('Settings save failed')
            return Response(
                {'error': 'An error occurred while saving settings.'},
                status=400,
            )


# Re-evaluate windowed playlists at most this often. Day-of-week and
# time-of-day boundaries don't show up in start_date/end_date, so we
# need a polling cap to ensure transitions are picked up. Mirrors
# ``anthias_viewer.scheduling.WINDOWED_DEADLINE_CAP_SECONDS``; once
# the C++ viewer consumes /api/v2/viewer/playlist (Phase 3 of GH
# #2906) the Python copy can be deleted.
_VIEWER_WINDOWED_DEADLINE_CAP_S = 60

_viewer_sysrandom = secrets.SystemRandom()


def _evaluate_viewer_playlist(
    now: datetime,
) -> tuple[list[Asset], datetime | None]:
    """Server-side equivalent of ``generate_asset_list()`` returning
    Asset rows (so ``AssetSerializerV2`` can serialise them) instead
    of plain dicts.

    Active filter and deadline computation share a single ``now`` so
    a row can't be filtered as active here while its end_date is
    skipped as past in the deadline pass — the two would otherwise
    disagree across a midnight tick.
    """
    candidates = list(
        Asset.objects.filter(
            is_enabled=True,
            start_date__isnull=False,
            end_date__isnull=False,
        ).order_by('play_order')
    )

    active_flags = [a.is_active(now=now) for a in candidates]
    active_assets = [a for a, ok in zip(candidates, active_flags) if ok]

    if settings['shuffle_playlist']:
        _viewer_sysrandom.shuffle(active_assets)

    deadline = _compute_viewer_deadline(candidates, active_flags, now)
    return active_assets, deadline


def _compute_viewer_deadline(
    assets: list[Asset],
    active_flags: list[bool],
    now: datetime,
) -> datetime | None:
    """Soonest future moment when the playlist might need refetching.

    Mirrors ``anthias_viewer.scheduling._compute_deadline``: past
    boundaries are dropped so a long-ago start_date on a window-
    blocked asset doesn't pin the deadline to "always overdue", and
    the windowed cap only applies while the asset is in its date
    range (the window can't toggle activeness outside of it).
    """
    candidates: list[datetime] = []
    has_windowed = False

    for asset, is_active in zip(assets, active_flags):
        boundary = asset.end_date if is_active else asset.start_date
        if boundary and boundary > now:
            candidates.append(boundary)
        if (
            asset.has_window_filter()
            and asset.start_date is not None
            and asset.end_date is not None
            and asset.start_date < now < asset.end_date
        ):
            has_windowed = True

    if has_windowed:
        candidates.append(
            now + timedelta(seconds=_VIEWER_WINDOWED_DEADLINE_CAP_S)
        )

    return min(candidates) if candidates else None


class ViewerPlaylistViewV2(APIView):
    """Active assets + next deadline, evaluated against server time.

    Intended for the C++ viewer (GH #2906 Phase 3) so the viewer no
    longer needs Django ORM access or its own ``Asset.is_active()``
    re-implementation. The Python viewer keeps using
    ``anthias_viewer.scheduling.generate_asset_list()`` directly until
    Phase 3 swaps it.

    Internal-auth gated for the same reason as
    ``AssetRecheckViewV2``: the viewer can't attach operator
    BasicAuth, so it presents the shared token derived from
    ``anthias.conf``.
    """

    @extend_schema(
        summary='Get the active playlist and next re-evaluation deadline',
        responses={200: ViewerPlaylistSerializerV2, 403: None},
    )
    def get(self, request: Request) -> Response:
        if not is_internal_request(request, settings):
            return Response(status=status.HTTP_403_FORBIDDEN)

        # Re-read anthias.conf for the same reason DeviceSettingsViewV2
        # does: an operator PATCH to /api/v2/device_settings updates the
        # on-disk file, and a viewer GET arriving before the in-memory
        # cache invalidates would otherwise shuffle (or not shuffle)
        # off the stale value. The PATCH path also broadcasts a Redis
        # ``reload`` so the viewer eventually catches up via the
        # subscriber, but reloading on read avoids a one-tick lag.
        try:
            settings.load()
        except Exception:
            logger.exception('Failed to reload settings for viewer playlist')

        now = timezone.now()
        active_assets, deadline = _evaluate_viewer_playlist(now)
        return Response(
            {
                # Pass ``now`` through context so AssetSerializerV2's
                # ``is_active`` field renders against the same instant
                # the filter used — without it a row right on a window
                # boundary could be returned in ``assets`` while its
                # ``is_active`` re-evaluates to False a few ms later.
                'assets': AssetSerializerV2(
                    active_assets, many=True, context={'now': now}
                ).data,
                'deadline': deadline,
                'now': now,
            }
        )


class ViewerSettingsViewV2(APIView):
    """Viewer-relevant settings subset for the C++ viewer.

    Narrower than ``DeviceSettingsViewV2`` on purpose: only the keys
    the viewer reads at runtime are exposed, so the internal-auth
    path doesn't surface operator credential fields. Internal-auth
    gated like ``ViewerPlaylistViewV2`` above.
    """

    @extend_schema(
        summary='Get viewer-relevant device settings',
        responses={200: ViewerSettingsSerializerV2, 403: None},
    )
    def get(self, request: Request) -> Response:
        if not is_internal_request(request, settings):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            settings.load()
        except Exception:
            logger.exception('Failed to reload settings for viewer')

        return Response(
            {
                'shuffle_playlist': settings['shuffle_playlist'],
                'show_splash': settings['show_splash'],
                'screen_rotation': clamp_screen_rotation(
                    settings['screen_rotation']
                ),
                'audio_output': settings['audio_output'],
                'debug_logging': settings['debug_logging'],
            }
        )


class InfoViewV2(InfoViewMixin):
    def get_anthias_version(self) -> str:
        # Composed in lib.diagnostics so HTML and API ship the same
        # label string. See get_anthias_version() for the format.
        return diagnostics.get_anthias_version()

    def get_device_model(self) -> str | int | None:
        device_model = device_helper.parse_cpu_info().get('model')

        if device_model is None and machine() == 'x86_64':
            device_model = 'Generic x86_64 Device'

        return device_model

    def get_uptime(self) -> dict[str, int | float]:
        system_uptime = timedelta(seconds=diagnostics.get_uptime())
        return {
            'days': system_uptime.days,
            'hours': round(system_uptime.seconds / 3600, 2),
        }

    def get_memory(self) -> dict[str, int | bool]:
        from anthias_common.board import LOW_RAM_THRESHOLD_KB

        virtual_memory = psutil.virtual_memory()
        # ``low_ram`` echoes the same gate that drives the viewer's
        # single-QWebEngineView mode and the asset processor's
        # 1080p upload cap. Exposed via /api/v2/info so external
        # clients can render a "degraded mode" badge alongside the
        # other memory fields. ``int | bool`` covers the field union;
        # ``bool`` is a subclass of ``int`` in Python so static
        # type-checkers don't flag the mixed dict.
        return {
            'total': virtual_memory.total >> 20,
            'used': virtual_memory.used >> 20,
            'free': virtual_memory.free >> 20,
            'shared': virtual_memory.shared >> 20,
            'buff': virtual_memory.buffers >> 20,
            'available': virtual_memory.available >> 20,
            'low_ram': virtual_memory.total < LOW_RAM_THRESHOLD_KB * 1024,
        }

    def get_ip_addresses(self) -> list[str]:
        # /api/v2/info is auth'd and not polled, so blocking on
        # get_node_ip()'s host-readiness loop is acceptable here —
        # the formatter still tolerates 'Unknown'/'Unable to retrieve
        # IP.' sentinels via _format_ip_urls. The polling endpoint
        # (NetworkIpAddressesViewV2) uses _safe_ip_addresses() instead,
        # which never blocks.
        return _format_ip_urls(get_node_ip())

    @extend_schema(
        summary='Get system information',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'viewlog': {'type': 'string'},
                    'loadavg': {'type': 'number'},
                    'free_space': {'type': 'string'},
                    'display_power': {'type': ['string', 'null']},
                    'up_to_date': {'type': 'boolean'},
                    'anthias_version': {'type': 'string'},
                    'device_model': {'type': 'string'},
                    'uptime': {
                        'type': 'object',
                        'properties': {
                            'days': {'type': 'integer'},
                            'hours': {'type': 'number'},
                        },
                    },
                    'memory': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'used': {'type': 'integer'},
                            'free': {'type': 'integer'},
                            'shared': {'type': 'integer'},
                            'buff': {'type': 'integer'},
                            'available': {'type': 'integer'},
                            'low_ram': {'type': 'boolean'},
                        },
                    },
                    'ip_addresses': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'mac_address': {'type': 'string'},
                    'host_user': {'type': 'string'},
                },
            }
        },
    )
    @authorized
    def get(self, request: Request) -> Response:
        viewlog = 'Not yet implemented'

        # Calculate disk space
        slash = statvfs('/')
        free_space = filesizeformat(slash.f_bavail * slash.f_frsize)
        display_power = r.get('display_power')

        return Response(
            {
                'viewlog': viewlog,
                'loadavg': diagnostics.get_load_avg()['15 min'],
                'free_space': free_space,
                'display_power': display_power,
                'up_to_date': is_up_to_date(),
                'anthias_version': self.get_anthias_version(),
                'device_model': self.get_device_model(),
                'uptime': self.get_uptime(),
                'memory': self.get_memory(),
                'ip_addresses': self.get_ip_addresses(),
                'mac_address': get_node_mac_address(),
                'host_user': getenv('HOST_USER'),
            }
        )


class IntegrationsViewV2(APIView):
    serializer_class = IntegrationsSerializerV2

    @extend_schema(
        summary='Get integrations information',
        responses={200: IntegrationsSerializerV2},
    )
    @authorized
    def get(self, request: Request) -> Response:
        data: dict[str, Any] = {
            'is_balena': is_balena_app(),
        }

        if data['is_balena']:
            data.update(
                {
                    'balena_device_id': getenv('BALENA_DEVICE_UUID'),
                    'balena_app_id': getenv('BALENA_APP_ID'),
                    'balena_app_name': getenv('BALENA_APP_NAME'),
                    'balena_supervisor_version': (
                        getenv('BALENA_SUPERVISOR_VERSION')
                    ),
                    'balena_host_os_version': (
                        getenv('BALENA_HOST_OS_VERSION')
                    ),
                    'balena_device_name_at_init': (
                        getenv('BALENA_DEVICE_NAME_AT_INIT')
                    ),
                }
            )

        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class ScreenlyValidateTokenViewV2(APIView):
    """Probe a Screenly v4.1 API token and reserve the migration group.

    Two steps in one round-trip because the wizard's UX is "click
    Continue → either an error or the asset picker". Validating the
    token alone would leave a follow-up call between Continue and the
    next screen, and the asset-group creation is cheap (idempotent
    get-or-create against ``/asset-groups``). The token is not stored;
    each request forwards it inline.
    """

    serializer_class = ScreenlyTokenSerializerV2

    @extend_schema(
        summary='Validate a Screenly v4.1 API token and prepare the '
        'migration asset-group',
        request=ScreenlyTokenSerializerV2,
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'valid': {'type': 'boolean'},
                    'asset_group_id': {
                        'type': 'string',
                        'nullable': True,
                    },
                    'asset_group_title': {
                        'type': 'string',
                        'nullable': True,
                    },
                    'error': {'type': 'string', 'nullable': True},
                },
            },
        },
    )
    @authorized
    def post(self, request: Request) -> Response:
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']

        try:
            valid = validate_token(token)
        except requests.RequestException:
            # Log the underlying transport error server-side for
            # diagnostics; don't echo it into the response — exception
            # detail can leak environment state (resolved hostnames,
            # proxy chain, socket-level errno text) that's not useful
            # to the operator and is flagged by CodeQL's information-
            # exposure check.
            logger.warning('Screenly token validate failed', exc_info=True)
            return Response(
                {
                    'valid': False,
                    'error': _SCREENLY_NETWORK_USER_MESSAGE,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if not valid:
            return Response({'valid': False})

        # Pre-create the destination group so the per-asset path is a
        # simple POST. Surfacing the failure here (rather than on every
        # asset) gives the operator one clear error instead of N noisy
        # ones if Screenly's group endpoint is misbehaving.
        #
        # On failure we return ``valid: False`` (rather than
        # ``valid: True`` paired with an error) so the field
        # consistently means "token validated AND the wizard can
        # proceed". A client that checks only ``valid`` won't be
        # tricked into showing the asset picker when group setup
        # actually failed.
        try:
            asset_group_id = ensure_asset_group(
                token, MIGRATION_ASSET_GROUP_TITLE
            )
        except ScreenlyMigrationError as error:
            # ``ScreenlyMigrationError`` messages are crafted in
            # ``screenly_migration`` for operator display (they wrap
            # Screenly's own error string, not Python exception state),
            # so exposing the .user_message attribute is intentional.
            return Response(
                {'valid': False, 'error': error.user_message},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except requests.RequestException:
            logger.warning('Screenly ensure_asset_group failed', exc_info=True)
            return Response(
                {
                    'valid': False,
                    'error': _SCREENLY_NETWORK_USER_MESSAGE,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                'valid': True,
                'asset_group_id': asset_group_id,
                'asset_group_title': MIGRATION_ASSET_GROUP_TITLE,
            }
        )


class ScreenlyMigrateAssetViewV2(APIView):
    """Forward a single Anthias asset to the configured Screenly account.

    Per-asset rather than batch on purpose: the UI shows live progress
    and continues past individual failures, which matches the way
    operators run this (large libraries, some assets reachable, some
    not). Batching would either swallow partial failures or hand-roll
    the same loop server-side with worse feedback.
    """

    serializer_class = ScreenlyMigrateAssetSerializerV2

    @extend_schema(
        summary='Migrate one asset to Screenly v4.1',
        request=ScreenlyMigrateAssetSerializerV2,
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'success': {'type': 'boolean'},
                    'screenly_asset_id': {
                        'type': 'string',
                        'nullable': True,
                    },
                    'error': {'type': 'string', 'nullable': True},
                },
            },
        },
    )
    @authorized
    def post(self, request: Request) -> Response:
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        asset_id = serializer.validated_data['asset_id']
        asset_group_id = (
            serializer.validated_data.get('asset_group_id') or None
        )

        try:
            asset = Asset.objects.get(pk=asset_id)
        except Asset.DoesNotExist:
            return Response(
                {'success': False, 'error': 'Asset not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            screenly_payload = migrate_asset(
                token, asset, asset_group_id=asset_group_id
            )
        except ScreenlyMigrationError as error:
            # See ScreenlyValidateTokenViewV2.post — .user_message is
            # the operator-display string we constructed in
            # screenly_migration; using it (rather than str(error))
            # keeps the response free of exception state.
            return Response({'success': False, 'error': error.user_message})
        except requests.RequestException:
            logger.warning(
                'Screenly migrate_asset failed for %s',
                asset_id,
                exc_info=True,
            )
            return Response(
                {
                    'success': False,
                    'error': _SCREENLY_NETWORK_USER_MESSAGE,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Screenly's create endpoint returns a postgREST-style array
        # with a single row when ``Prefer: return=representation`` is
        # set. Unwrap to the row before reading ``id``.
        row: dict[str, Any] = {}
        if isinstance(screenly_payload, list) and screenly_payload:
            first = screenly_payload[0]
            if isinstance(first, dict):
                row = first
        elif isinstance(screenly_payload, dict):
            row = screenly_payload

        # Be defensive on the field name — we want the UI to keep
        # working if Screenly renames the id key.
        screenly_asset_id = (
            row.get('id') or row.get('asset_id') or row.get('ulid')
        )

        return Response(
            {
                'success': True,
                'screenly_asset_id': screenly_asset_id,
            }
        )
