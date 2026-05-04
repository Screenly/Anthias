import logging
import tarfile
import uuid
from datetime import datetime
from mimetypes import guess_type
from os import path, remove
from urllib.parse import urlparse, urlunparse

from django.contrib import messages
from django.http import FileResponse, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from anthias_server.app import page_context
from anthias_server.celery_tasks import reboot_anthias, shutdown_anthias
from anthias_server.lib import backup_helper, diagnostics
from anthias_server.lib.auth import authorized
from anthias_common.utils import (
    connect_to_redis,
)
from anthias_server.settings import ViewerPublisher, settings

from .helpers import (
    add_default_assets,
    remove_default_assets,
    template,
)

logger = logging.getLogger(__name__)
r = connect_to_redis()


_ANTHIAS_REPO_URL = 'https://github.com/Screenly/Anthias'


def _parse_local_datetime(value: str) -> datetime:
    """Parse a date/time the edit-form posts back from Flatpickr.

    Flatpickr formats the value using whatever format we asked for
    (m/d/Y h:i K, d-m-Y H:i, Y/m/d, etc.) — i.e. the device's
    configured date_format + use_24_hour_clock pair. Try the active
    set of formats here, fall back to ISO fromisoformat() so any
    pre-existing rows / API-side writes still parse cleanly.
    """
    settings.load()
    df = settings['date_format']
    date_part_map = {
        'mm/dd/yyyy': '%m/%d/%Y',
        'dd/mm/yyyy': '%d/%m/%Y',
        'yyyy/mm/dd': '%Y/%m/%d',
        'mm-dd-yyyy': '%m-%d-%Y',
        'dd-mm-yyyy': '%d-%m-%Y',
        'yyyy-mm-dd': '%Y-%m-%d',
        'mm.dd.yyyy': '%m.%d.%Y',
        'dd.mm.yyyy': '%d.%m.%Y',
        'yyyy.mm.dd': '%Y.%m.%d',
    }
    date_fmt = date_part_map.get(df, '%m/%d/%Y')
    time_fmt = '%H:%M' if settings['use_24_hour_clock'] else '%I:%M %p'
    candidates = [f'{date_fmt} {time_fmt}', f'{date_fmt} %H:%M']
    for fmt in candidates:
        try:
            return timezone.make_aware(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return timezone.make_aware(datetime.fromisoformat(value))


def _prettify_upload_name(filename: str) -> str:
    """Turn 'My_day-2.mp4' into 'My Day 2'.

    Strips the file extension, normalises common separators
    (underscores, hyphens, dots within the stem) into spaces, collapses
    repeated whitespace, and title-cases the result. Empty input or a
    bare extension falls back to the original filename so the operator
    never gets a blank Asset.name."""
    if not filename:
        return filename
    stem, _ext = path.splitext(filename)
    if not stem:
        return filename
    # Drop any leading dot from a hidden file once the extension is
    # gone (e.g. `.hidden.mp4` → `.hidden` → `hidden`).
    cleaned = stem.lstrip('.')
    cleaned = cleaned.replace('_', ' ').replace('-', ' ').replace('.', ' ')
    cleaned = ' '.join(cleaned.split())
    if not cleaned:
        return filename
    return cleaned.title()


def _checkbox(post: HttpRequest, name: str) -> bool:
    """Read a HTML checkbox: present-and-truthy = True, otherwise False.

    The settings form pairs every checkbox with a hidden 'false' input
    of the same name so unchecked boxes still POST a value. Django's
    QueryDict.getlist returns both, last wins — pick the last 'true'.
    """
    return 'true' in post.POST.getlist(name)


@authorized
@require_http_methods(['GET'])
def integrations(request: HttpRequest) -> HttpResponse:
    context = page_context.integrations()
    context['active_nav'] = 'integrations'
    return template(request, 'integrations.html', context)


# --- /home (Schedule Overview) ----------------------------------------------


@authorized
@require_http_methods(['GET'])
def home(request: HttpRequest) -> HttpResponse:
    context = page_context.assets()
    context['active_nav'] = 'home'
    return template(request, 'home.html', context)


@authorized
@require_http_methods(['GET'])
def assets_table_partial(request: HttpRequest) -> HttpResponse:
    """HTMX endpoint for the table area only — re-rendered every 5s
    by the home page and after every successful write."""
    from django.shortcuts import render as _render

    return _render(request, '_asset_table.html', page_context.assets())


@authorized
@require_http_methods(['POST'])
def assets_create(request: HttpRequest) -> HttpResponse:
    """URI-based asset add (the Add modal's URI tab). Mirrors the
    minimum field-set the React modal sent: uri, derived mimetype,
    default duration window.

    YouTube URLs are special-cased: rather than persisting the URL as
    a webpage (which YouTube's referrer policy renders as a broken
    embed), the row is created as ``mimetype='video'`` pointing at a
    yet-to-be-downloaded local mp4, and ``download_youtube_asset`` is
    queued to fetch the file. The "Processing" pill on the table row
    clears once the worker completes.
    """
    from anthias_common.utils import validate_url
    from anthias_common.youtube import (
        dispatch_download,
        is_youtube_url,
        youtube_destination_path,
    )
    from anthias_server.app.models import Asset
    from datetime import timedelta

    uri = (request.POST.get('uri') or '').strip()
    if not uri or not validate_url(uri):
        messages.error(request, 'Invalid URL.')
        return _asset_table_response(request)

    # Best-effort mimetype guess from extension; default to webpage.
    # YouTube takes priority — its watch URLs end in `?v=…` not a
    # video extension, so the file-extension heuristic below would
    # otherwise classify them as a webpage.
    is_youtube = is_youtube_url(uri)
    if is_youtube:
        mimetype = 'video'
    else:
        mimetype = 'webpage'
        lower = uri.lower()
        if lower.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg')):
            mimetype = 'image'
        elif lower.endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi', '.flv')):
            mimetype = 'video'

    now = timezone.now()
    # New assets land at the end of the active playlist instead of
    # always-position-zero, so a fresh upload doesn't yank ordering
    # from under everything else.
    # Count is_enabled rows (the same partition the home page uses to
    # split Active vs Inactive). is_active() also factors in the date
    # range and play_window, which would silently shrink play_order
    # past the visible row count whenever today's weekday excluded
    # some scheduled assets.
    play_order = Asset.objects.filter(
        is_enabled=True, is_processing=False
    ).count()

    if is_youtube:
        # Generate the asset_id up front so the row's URI can point
        # at the destination path the Celery task will write to.
        # Title + duration land on the row when the task completes;
        # the placeholder name is the URL the operator pasted, which
        # is at least recognisable in the table while processing.
        asset_id = uuid.uuid4().hex
        asset = Asset.objects.create(
            asset_id=asset_id,
            name=uri,
            uri=youtube_destination_path(asset_id, settings),
            mimetype='video',
            duration=0,
            is_enabled=True,
            is_processing=True,
            play_order=play_order,
            start_date=now,
            end_date=now + timedelta(days=30),
        )
        dispatch_download(asset.asset_id, uri)
        return _asset_table_response(
            request,
            toast=('info', 'Downloading YouTube video…'),
        )

    Asset.objects.create(
        name=uri,
        uri=uri,
        mimetype=mimetype,
        duration=settings['default_duration'],
        is_enabled=True,
        is_processing=False,
        play_order=play_order,
        start_date=now,
        end_date=now + timedelta(days=30),
    )
    return _asset_table_response(request, toast=('success', 'Asset added'))


@authorized
@require_http_methods(['POST'])
def assets_upload(request: HttpRequest) -> HttpResponse:
    """File upload tab. Mirrors api.views.mixins.FileAssetViewMixin.post:
    move the upload into assetdir, create an Asset row, return the
    table partial so HTMX can swap straight in."""
    from anthias_server.app.models import Asset
    from datetime import timedelta

    file_upload = request.FILES.get('file_upload')
    if file_upload is None or not file_upload.name:
        messages.error(request, 'No file uploaded.')
        return _asset_table_response(request)

    upload_name: str = file_upload.name
    file_type = guess_type(upload_name)[0] or ''
    if file_type.split('/')[0] not in ('image', 'video'):
        messages.error(request, 'Invalid file type. Expected image or video.')
        return _asset_table_response(request)

    # Operator-friendly display name: 'My_day-2.mp4' → 'My Day 2'.
    # Drops the extension (the row already carries mimetype) and
    # title-cases the stem so the schedule reads cleanly. The original
    # filename still rides along on the upload toast so the operator
    # has a breadcrumb back to what they picked.
    display_name = _prettify_upload_name(upload_name)

    # uuid4 (random) instead of uuid5(NAMESPACE_URL, name): the
    # deterministic v5 form would collide on disk for two files
    # uploaded with the same name (different content), silently
    # overwriting the older one.
    final_name = uuid.uuid4().hex
    final_path = path.join(settings['assetdir'], final_name)
    with open(final_path, 'wb') as f:
        for chunk in file_upload.chunks():
            f.write(chunk)

    mimetype = file_type.split('/')[0]
    # Video duration is resolved out of band by the
    # `probe_video_duration` Celery task — ffprobe can take several
    # seconds on a Pi 1/Zero and we don't want the upload POST to block
    # the operator's modal that long. The row is created with
    # is_processing=True so the table renders a "Processing" pill until
    # the worker writes the real duration back; the API's v2 path uses
    # the same convention. Images keep the configured default.
    duration = settings['default_duration']
    is_video = mimetype == 'video'

    now = timezone.now()
    # Count is_enabled rows (the same partition the home page uses to
    # split Active vs Inactive). is_active() also factors in the date
    # range and play_window, which would silently shrink play_order
    # past the visible row count whenever today's weekday excluded
    # some scheduled assets.
    play_order = Asset.objects.filter(
        is_enabled=True, is_processing=False
    ).count()
    asset = Asset.objects.create(
        name=display_name,
        uri=final_path,
        mimetype=mimetype,
        duration=duration,
        is_enabled=True,
        is_processing=is_video,
        play_order=play_order,
        start_date=now,
        end_date=now + timedelta(days=30),
    )
    if is_video:
        from anthias_server.celery_tasks import probe_video_duration

        probe_video_duration.delay(asset.asset_id)
        return _asset_table_response(
            request,
            toast=(
                'info',
                f'Uploaded {upload_name} — analysing video…',
            ),
        )
    return _asset_table_response(
        request, toast=('success', f'Uploaded {upload_name}')
    )


@authorized
@require_http_methods(['POST'])
def assets_update(request: HttpRequest, asset_id: str) -> HttpResponse:
    from anthias_server.app.models import Asset

    asset = Asset.objects.filter(asset_id=asset_id).first()
    if asset is None:
        return _asset_table_response(request)

    asset.name = request.POST.get('name', asset.name)
    # mimetype is intentionally NOT pulled from the POST: it's derived
    # from the URI/file at create time, and accepting a client-supplied
    # update would let the row desync from the actual content (an image
    # row marked as 'webpage', etc.). The edit modal renders mimetype
    # read-only for the same reason.
    if asset.mimetype == 'video':
        # Video duration is owned by the probe_video_duration Celery
        # task, not the edit form. Leave the persisted value alone so
        # an edit doesn't clobber the probed length back to 0. The
        # edit form already disables the duration input for videos
        # (`:disabled="editAsset.mimetype === 'video'"`); this branch
        # is the matching server-side guard against hand-crafted POSTs
        # that try to write a duration anyway.
        pass
    else:
        asset.duration = int(
            request.POST.get('duration') or asset.duration or 0
        )
    start = request.POST.get('start_date')
    end = request.POST.get('end_date')
    if start:
        asset.start_date = _parse_local_datetime(start)
    if end:
        asset.end_date = _parse_local_datetime(end)
    asset.nocache = _checkbox(request, 'nocache')
    asset.skip_asset_check = _checkbox(request, 'skip_asset_check')

    # Day-of-week filter — POST sends one value per checked weekday
    # (1=Mon..7=Sun, ISO). Empty / unchecked-all means "every day", same
    # convention Asset.get_play_days() falls back to. Persist as JSON
    # so Asset.get_play_days() (which json.loads the field on read) can
    # round-trip cleanly.
    import json as _json

    raw_days = request.POST.getlist('play_days')
    parsed_days: list[int] = []
    for d in raw_days:
        try:
            n = int(d)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 7:
            parsed_days.append(n)
    asset.play_days = _json.dumps(
        sorted(set(parsed_days)) or [1, 2, 3, 4, 5, 6, 7]
    )

    # Time-of-day window. Reject partial windows (only one endpoint
    # set) the same way _validate_time_window does on the API path.
    play_from = (request.POST.get('play_time_from') or '').strip()
    play_to = (request.POST.get('play_time_to') or '').strip()
    if play_from and play_to:
        from datetime import time as _time

        h1, m1 = play_from.split(':')[:2]
        h2, m2 = play_to.split(':')[:2]
        asset.play_time_from = _time(int(h1), int(m1))
        asset.play_time_to = _time(int(h2), int(m2))
    else:
        asset.play_time_from = None
        asset.play_time_to = None

    asset.save()
    ViewerPublisher.get_instance().send_to_viewer('reload')
    return _asset_table_response(request, toast=('success', 'Changes saved'))


@authorized
@require_http_methods(['POST'])
def assets_toggle(request: HttpRequest, asset_id: str) -> HttpResponse:
    from anthias_server.app.models import Asset

    toast: tuple[str, str] | None = None
    asset = Asset.objects.filter(asset_id=asset_id).first()
    if asset is not None:
        asset.is_enabled = not asset.is_enabled
        asset.save()
        ViewerPublisher.get_instance().send_to_viewer('reload')
        toast = (
            'success',
            f'Asset {"enabled" if asset.is_enabled else "disabled"}',
        )
    return _asset_table_response(request, toast=toast)


@authorized
@require_http_methods(['POST'])
def assets_delete(request: HttpRequest, asset_id: str) -> HttpResponse:
    from anthias_server.app.models import Asset

    Asset.objects.filter(asset_id=asset_id).delete()
    ViewerPublisher.get_instance().send_to_viewer('reload')
    return _asset_table_response(request, toast=('success', 'Asset deleted'))


@authorized
@require_http_methods(['POST'])
def assets_order(request: HttpRequest) -> HttpResponse:
    """Mirrors api.helpers.save_active_assets_ordering — same comma-csv
    body that React's @dnd-kit handler POSTs."""
    from anthias_server.api.helpers import save_active_assets_ordering

    ids = [i for i in request.POST.get('ids', '').split(',') if i]
    save_active_assets_ordering(ids)
    ViewerPublisher.get_instance().send_to_viewer('reload')
    return _asset_table_response(request)


def _safe_redirect_uri(uri: str) -> str | None:
    """Defang asset.uri before handing it to redirect().

    Threat model: the asset row is operator-controlled (authenticated
    session, gated by @authorized on the calling endpoint). The risk
    we mitigate is a hostile-but-authenticated operator stashing a
    `javascript:` / `data:` URI on an asset and tricking a colleague
    into a "download"-link click that runs script in their session
    against the management UI's origin — i.e. a stored XSS via the
    redirect sink.

    Defenses:
      1. Allowlist schemes to plain http / https only. javascript:,
         data:, vbscript:, file:, about: etc. are all rejected.
      2. Require a netloc. `http:///foo` and friends parse with an
         empty host; redirect() would resolve them as same-origin
         relative paths.

    Operators legitimately store http:// URIs for LAN-only signage
    (intranet pages, RTSP/RTMP gateways) where TLS isn't terminated,
    so we accept http alongside https.
    """
    if not uri:
        return None
    parsed = urlparse(uri.strip())
    # `scheme` and `netloc` are both mandatory — empty either means
    # the input wasn't a fully qualified URL we can safely send the
    # browser to.
    if parsed.scheme.lower() not in ('http', 'https'):  # NOSONAR(S5332)
        return None
    if not parsed.netloc:
        return None
    # Reconstruct from the parsed components instead of returning the
    # raw input string. Static analysers (CodeQL py/url-redirection)
    # treat urlparse + urlunparse as a sanitization step because the
    # output URL is built from validated parts, not concatenated user
    # input — even though the resulting string is byte-equivalent.
    return urlunparse(parsed)


def _safe_local_asset_path(uri: str) -> str | None:
    """Resolve uri to a real path under settings['assetdir'].

    Mirrors the realpath + startswith guard that views_files.anthias_assets
    uses for the public asset URL prefix. CodeQL flags the raw
    open(asset.uri) calls as path-traversal sinks even though the
    upload view writes filenames as uuid4().hex; defending here means
    a hand-crafted DB row can't escape the assets directory.
    """
    if not uri:
        return None
    base = path.realpath(settings['assetdir'])
    # Rebuild the candidate from a trusted base + the basename of the
    # stored URI. The basename strips any '..' or absolute prefix the
    # operator could have written into the DB, leaving a single
    # filename component that can only refer to a file under base.
    # CodeQL's py/path-injection sees this `basename → join → realpath
    # → startswith` pattern as a recognised sanitisation step.
    filename = path.basename(uri)
    if not filename or filename in ('.', '..'):
        return None
    candidate = path.realpath(path.join(base, filename))
    if not candidate.startswith(base + path.sep):
        return None
    if not path.isfile(candidate):
        return None
    return candidate


@authorized
@require_http_methods(['GET'])
def assets_download(request: HttpRequest, asset_id: str) -> HttpResponseBase:
    """Stream the asset's content back the way React's download button
    did: redirect to the URL for url-mimetypes, FileResponse for files."""
    from anthias_server.app.models import Asset

    asset = Asset.objects.filter(asset_id=asset_id).first()
    if asset is None or not asset.uri:
        return redirect(reverse('anthias_app:home'))
    if asset.mimetype in ('webpage', 'streaming'):
        safe = _safe_redirect_uri(asset.uri)
        # `safe` is whitelisted to http(s):// schemes by _safe_redirect_uri,
        # and the endpoint is gated by @authorized (only operators with a
        # session reach this). The redirect target is the operator's own
        # asset URI — that's the feature, not a sink.
        return (  # lgtm [py/url-redirection]
            redirect(safe) if safe else redirect(reverse('anthias_app:home'))
        )
    safe_path = _safe_local_asset_path(asset.uri)
    if safe_path is None:
        return redirect(reverse('anthias_app:home'))
    # _safe_local_asset_path realpaths the URI and verifies it lives
    # under settings['assetdir'] before returning, so the open() call
    # cannot escape the assets directory.
    return FileResponse(
        open(safe_path, 'rb'), as_attachment=True
    )  # lgtm [py/path-injection]


@authorized
@require_http_methods(['GET'])
def assets_preview(request: HttpRequest, asset_id: str) -> HttpResponseBase:
    """Serve an uploaded asset inline so the preview modal can embed it
    in <img>/<video>. URL-typed assets (webpage/streaming) redirect to
    the source URI; the modal renders those in an <iframe>."""
    from anthias_server.app.models import Asset

    asset = Asset.objects.filter(asset_id=asset_id).first()
    if asset is None or not asset.uri:
        return redirect(reverse('anthias_app:home'))
    if asset.mimetype in ('webpage', 'streaming'):
        safe = _safe_redirect_uri(asset.uri)
        # `safe` is whitelisted to http(s):// schemes by _safe_redirect_uri,
        # and the endpoint is gated by @authorized (only operators with a
        # session reach this). The redirect target is the operator's own
        # asset URI — that's the feature, not a sink.
        return (  # lgtm [py/url-redirection]
            redirect(safe) if safe else redirect(reverse('anthias_app:home'))
        )
    safe_path = _safe_local_asset_path(asset.uri)
    if safe_path is None:
        return redirect(reverse('anthias_app:home'))
    return FileResponse(
        open(safe_path, 'rb'), as_attachment=False
    )  # lgtm [py/path-injection]


@authorized
@require_http_methods(['POST'])
def assets_control(request: HttpRequest, command: str) -> HttpResponse:
    """Previous / Next playback. Dispatches the same Redis pub/sub
    command the API mixin sends."""
    if command in ('previous', 'next'):
        ViewerPublisher.get_instance().send_to_viewer(f'asset_{command}')
    return redirect(reverse('anthias_app:home'))


def _asset_table_response(
    request: HttpRequest,
    *,
    toast: tuple[str, str] | None = None,
) -> HttpResponse:
    """Shared response helper for all write endpoints.

    HTMX requests (hx-post) get the swapped partial; plain form
    submits fall back to a redirect so the page reloads end-to-end.

    Pass `toast=("success", "Asset deleted")` to fire a client toast
    via the HX-Trigger header (HTMX requests) or a Django flash
    message (full-page reload)."""
    # Fan out a refresh nudge over the WebSocket so other browsers
    # currently looking at the home page repaint without waiting for
    # their next 5s poll. Skipped on read endpoints (those don't call
    # this helper at all). Failures are swallowed inside notify_asset_update.
    from anthias_server.app.consumers import notify_asset_update

    notify_asset_update()

    if request.headers.get('HX-Request'):
        from django.shortcuts import render as _render

        response = _render(request, '_asset_table.html', page_context.assets())
        if toast is not None:
            _set_toast_header(response, toast[0], toast[1])
        return response

    if toast is not None:
        _msg_fn = {
            'success': messages.success,
            'error': messages.error,
            'info': messages.info,
        }.get(toast[0], messages.info)
        _msg_fn(request, toast[1])
    return redirect(reverse('anthias_app:home'))


def _set_toast_header(response: HttpResponse, kind: str, message: str) -> None:
    """Attach an HX-Trigger header so the global Alpine store
    (registered in vendor.ts) renders a toast for this response."""
    import json as _json

    payload = {'toast': {'kind': kind, 'message': message}}
    # Merge with any existing HX-Trigger so callers stacking triggers
    # don't clobber each other.
    existing = response.headers.get('HX-Trigger')
    if existing:
        try:
            merged = _json.loads(existing)
            if isinstance(merged, dict):
                merged.update(payload)
                payload = merged
        except _json.JSONDecodeError:
            # Existing value was a bare event name, not JSON — drop
            # it; the toast payload supersedes for our use case.
            pass
    response['HX-Trigger'] = _json.dumps(payload)


@authorized
@require_http_methods(['GET'])
def settings_view(request: HttpRequest) -> HttpResponse:
    context = page_context.device_settings()
    context['active_nav'] = 'settings'
    return template(request, 'settings.html', context)


@authorized
@require_http_methods(['POST'])
def settings_save(request: HttpRequest) -> HttpResponse:
    """Mirror of api.views.v2.DeviceSettingsViewV2.patch.

    Touches the same settings primitives the API view does so the JSON
    and HTML write paths stay aligned without re-routing through HTTP.
    """
    settings.load()
    auth_backend = request.POST.get('auth_backend', '')
    current_password = request.POST.get('current_password', '')

    try:
        if (
            auth_backend != settings['auth_backend']
            and settings['auth_backend']
        ):
            if not current_password:
                raise ValueError(
                    'Must supply current password to change '
                    'authentication method'
                )
            auth = settings.auth
            if auth is None or not auth.check_password(current_password):
                raise ValueError('Incorrect current password.')

        prev_auth_backend = settings['auth_backend']
        if not current_password and prev_auth_backend:
            current_pass_correct = None
        else:
            current_pass_correct = settings.auth_backends[
                prev_auth_backend
            ].check_password(current_password)
        # Mirror api.views.v2.DeviceSettingsViewV2.update_auth_settings
        # inline rather than reaching for the per-backend update_settings —
        # the backend's signature takes a DRF request, not the form-POST
        # dict we've got, and its v2 implementation lives in the API view.
        if auth_backend == 'auth_basic':
            new_user = request.POST.get('user', '')
            new_pass = request.POST.get('password', '')
            new_pass_2 = request.POST.get('password_2', '')

            from anthias_server.lib.auth import hash_password

            if settings['password']:
                if new_user != settings['user']:
                    if current_pass_correct is None:
                        raise ValueError(
                            'Must supply current password to change username'
                        )
                    if not current_pass_correct:
                        raise ValueError('Incorrect current password.')
                    settings['user'] = new_user
                if new_pass:
                    if current_pass_correct is None:
                        raise ValueError(
                            'Must supply current password to change password'
                        )
                    if not current_pass_correct:
                        raise ValueError('Incorrect current password.')
                    if new_pass_2 != new_pass:
                        raise ValueError('New passwords do not match!')
                    settings['password'] = hash_password(new_pass)
            else:
                if new_user:
                    if new_pass and new_pass != new_pass_2:
                        raise ValueError('New passwords do not match!')
                    if not new_pass:
                        raise ValueError('Must provide password')
                    settings['user'] = new_user
                    settings['password'] = hash_password(new_pass)
                elif current_pass_correct is None:
                    # Switching to basic auth without a username is a no-op
                    # — fall through and let auth_backend save below. Same
                    # behaviour the API view falls into when its
                    # update_auth_settings sees an empty username.
                    pass
        settings['auth_backend'] = auth_backend
        # current_pass_correct is consumed above; reference it so the
        # local doesn't read as unused.
        _ = current_pass_correct

        settings['player_name'] = request.POST.get('player_name', '')
        settings['default_duration'] = int(
            request.POST.get('default_duration') or 0
        )
        settings['default_streaming_duration'] = int(
            request.POST.get('default_streaming_duration') or 0
        )
        settings['audio_output'] = request.POST.get('audio_output', 'hdmi')
        settings['date_format'] = request.POST.get('date_format', 'mm/dd/yyyy')

        new_default_assets = _checkbox(request, 'default_assets')
        if new_default_assets and not settings['default_assets']:
            add_default_assets()
        elif not new_default_assets and settings['default_assets']:
            remove_default_assets()
        settings['default_assets'] = new_default_assets

        settings['show_splash'] = _checkbox(request, 'show_splash')
        settings['shuffle_playlist'] = _checkbox(request, 'shuffle_playlist')
        settings['use_24_hour_clock'] = _checkbox(request, 'use_24_hour_clock')
        settings['debug_logging'] = _checkbox(request, 'debug_logging')

        settings.save()
        ViewerPublisher.get_instance().send_to_viewer('reload')

        messages.success(request, 'Settings were successfully saved.')
    except Exception as exc:
        logger.exception('Settings save failed')
        messages.error(request, str(exc) or 'Failed to save settings.')

    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_backup(request: HttpRequest) -> HttpResponseBase:
    """Same as api.views.mixins.BackupViewMixin.post but streams the
    archive back inline instead of returning the filename + relying
    on a follow-up /static_with_mime/ fetch."""
    from os import getenv

    filename = backup_helper.create_backup(name=settings['player_name'])
    # backup_helper.create_backup writes to $HOME/anthias/staticfiles/.
    # Reach for the same base here so we serve the archive from the
    # path it was actually written to. The pre-fix path.join('static',
    # filename) was relative to CWD and would FileNotFoundError in
    # production where uvicorn runs out of /usr/src/app.
    archive_path = path.join(
        getenv('HOME') or '', 'anthias/staticfiles', filename
    )
    response = FileResponse(
        open(archive_path, 'rb'),
        as_attachment=True,
        filename=filename,
        content_type='application/x-tgz',
    )
    return response


@authorized
@require_http_methods(['POST'])
def settings_recover(request: HttpRequest) -> HttpResponse:
    publisher = ViewerPublisher.get_instance()
    file_upload = request.FILES.get('backup_upload')
    if file_upload is None or not file_upload.name:
        messages.error(request, 'No backup file uploaded.')
        return redirect(reverse('anthias_app:settings'))

    if guess_type(file_upload.name)[0] != 'application/x-tar':
        messages.error(request, 'Incorrect file extension.')
        return redirect(reverse('anthias_app:settings'))

    # Server-side filename to defend against path traversal in the
    # client-supplied name (mirrors RecoverViewMixin).
    location = path.join('static', f'{uuid.uuid4().hex}.tar.gz')
    try:
        publisher.send_to_viewer('stop')
        with open(location, 'wb') as f:
            for chunk in file_upload.chunks():
                f.write(chunk)
        try:
            backup_helper.recover(location)
            messages.success(request, 'Recovery successful.')
        except (backup_helper.BackupRecoverError, tarfile.TarError):
            logger.exception('Backup recovery failed')
            messages.error(request, 'Invalid backup archive.')
    finally:
        if path.isfile(location):
            try:
                remove(location)
            except OSError:
                logger.exception(
                    'Failed to remove leftover backup upload at %s', location
                )
        publisher.send_to_viewer('play')

    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_reboot(request: HttpRequest) -> HttpResponse:
    reboot_anthias.apply_async()
    messages.success(request, 'Reboot has started successfully.')
    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_shutdown(request: HttpRequest) -> HttpResponse:
    shutdown_anthias.apply_async()
    messages.success(
        request,
        'Device shutdown has started successfully. '
        'Soon you will be able to unplug the power from your Raspberry Pi.',
    )
    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['GET'])
def system_info(request: HttpRequest) -> HttpResponse:
    context = page_context.system_info()
    # Master-branch builds get a clickable link to the commit; other
    # branches stay as plain text (mirrors AnthiasVersionValue in the
    # old React component, which only built the link when branch==master).
    # Read git pieces straight off the env so we don't have to re-parse
    # the version label in lib.diagnostics.get_anthias_version().
    branch = diagnostics.get_git_branch() or ''
    commit = diagnostics.get_git_short_hash() or ''
    if branch == 'master' and commit:
        context['anthias_version_master_link'] = (
            f'{_ANTHIAS_REPO_URL}/commit/{commit}'
        )
    context['active_nav'] = 'system-info'
    return template(request, 'system_info.html', context)


def _safe_login_next(request: HttpRequest, candidate: str) -> str:
    """Whitelist `next` to same-host paths so the login flow can't be
    weaponised as an open redirect. Falls back to the dashboard for
    empty / off-host / scheme-mismatched values."""
    home = reverse('anthias_app:home')
    if not candidate:
        return home
    if url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return home


@require_http_methods(['GET', 'POST'])
def login(request: HttpRequest) -> HttpResponse:
    # Read `next` from the form on POST (login.html round-trips the
    # query-string value through a hidden input) and from the query
    # string on GET (initial render after a 401/403 redirect).
    next_url = (
        request.POST.get('next')
        if request.method == 'POST'
        else request.GET.get('next')
    ) or ''

    if request.method == 'POST':
        username = request.POST.get('username') or ''
        password = request.POST.get('password') or ''

        auth = settings.auth
        if (
            auth is not None
            and hasattr(auth, '_check')
            and auth._check(username, password)
        ):
            # Store credentials in session
            request.session['auth_username'] = username
            request.session['auth_password'] = password

            return redirect(_safe_login_next(request, next_url))
        else:
            messages.error(request, 'Invalid username or password')
            return template(request, 'login.html', {'next': next_url})

    return template(request, 'login.html', {'next': next_url})


@require_http_methods(['GET'])
def splash_page(request: HttpRequest) -> HttpResponse:
    # IPs are populated client-side by polling /api/v2/network/ip-addresses
    # so the page renders immediately even when the host bus is slow on
    # first boot, and updates if IPs change during the splash's display
    # window (e.g. a DHCP renewal mid-splash). This also avoids the
    # historical `ipaddress.ip_address('Unknown')` crash that took the
    # whole render down on a flaky Balena supervisor.
    return template(
        request,
        'splash-page.html',
        {
            'splash_logo_url': settings['splash_logo_url'],
        },
    )
