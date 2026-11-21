import ipaddress
import mimetypes
import os
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
)
from django.http.response import HttpResponseBase
from django.views.decorators.http import require_GET

# Defense-in-depth, not the real perimeter. Caddy fronts every install
# and rewrites X-Forwarded-For with the real client address, which
# uvicorn honours (FORWARDED_ALLOW_IPS=*), so the checks below see the
# LAN client. The RFC1918 gate therefore admits the whole LAN, which is
# why static_with_mime also requires authentication.
# IPs below are the standard RFC1918 / Docker-bridge ranges, hardcoded
# on purpose — Sonar's S1313 ("don't hardcode IPs") doesn't apply.
ANTHIAS_ASSETS_ROOT = Path('/data/anthias_assets')
STATIC_FILES_ROOT = Path('/data/anthias/staticfiles')

DOCKER_BRIDGE_CIDR = ipaddress.ip_network('172.16.0.0/12')  # NOSONAR
RFC1918_CIDRS = (
    ipaddress.ip_network('10.0.0.0/8'),  # NOSONAR
    ipaddress.ip_network('172.16.0.0/12'),  # NOSONAR
    ipaddress.ip_network('192.168.0.0/16'),  # NOSONAR
)

# Allowlisted Content-Types for the ?mime= query parameter on
# /static_with_mime/. Letting the caller set an arbitrary value would
# turn a stored static file into XSS the moment anything else (e.g. a
# future feature) writes user-controllable content under STATIC_ROOT.
# The frontend's only legitimate use is forcing a tarball download for
# DB backups (`application/x-tgz`); the rest are conservative aliases
# in case a future caller needs them.
STATIC_WITH_MIME_ALLOWED_TYPES = frozenset(
    {
        'application/gzip',
        'application/octet-stream',
        'application/x-gzip',
        'application/x-tar',
        'application/x-tgz',
        'application/zip',
    }
)


ViewFunc = Callable[..., HttpResponseBase]


def _client_ip(
    request: HttpRequest,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(request.META.get('REMOTE_ADDR', ''))


def require_client_in(
    *cidrs: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> Callable[[ViewFunc], ViewFunc]:
    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        def wrapper(
            request: HttpRequest, *args: Any, **kwargs: Any
        ) -> HttpResponseBase:
            try:
                addr = _client_ip(request)
            except ValueError:
                return HttpResponseForbidden()
            if not any(addr in cidr for cidr in cidrs):
                return HttpResponseForbidden()
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@require_GET
@require_client_in(DOCKER_BRIDGE_CIDR)
def anthias_assets(request: HttpRequest, filename: str) -> HttpResponseBase:
    # Trailing os.sep on `base` is required so e.g.
    # '/data/anthias_assets_evil/...' doesn't slip past startswith().
    base = os.path.realpath(ANTHIAS_ASSETS_ROOT) + os.sep
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base):
        raise Http404
    # Not everything in the asset dir is an asset, and the rest of it
    # was never meant to be fetchable — while the CIDR gate above does
    # not exclude LAN clients in the default no-SSL install. Three
    # kinds of transient file live here:
    #
    #   * `.uploads/<id>.part`      — a chunked browser upload
    #   * `.import-<hex><ext>`      — a content import, and its `.part`
    #   * `<upload_id>.tmp`         — a resumable REST API upload
    #   * `<uuid>.<ext>.part`       — a yt-dlp or remote-video download
    #                                 in progress (celery_tasks)
    #
    # Assets are always `<uuid>.<ext>`, and never `.tmp` or `.part`:
    # _safe_ext refuses those as source extensions precisely so the
    # celery sweep (which deletes `*.tmp` on sight) and the backup
    # filter cannot swallow a real asset. So both rules below refuse
    # transient files and nothing else.
    relative = os.path.relpath(target, base)
    if any(part.startswith('.') for part in relative.split(os.sep)):
        raise Http404
    if relative.endswith(('.tmp', '.part')):
        raise Http404
    try:
        return FileResponse(open(target, 'rb'))
    except (FileNotFoundError, IsADirectoryError):
        raise Http404


def _basic_auth_user(request: HttpRequest) -> Any:
    """The user named by a valid ``Authorization: Basic`` header, if any.

    ``POST /api/v2/backup`` hands a Basic-auth client a filename to
    fetch from here, so Basic keeps working for that download. Bearer
    tokens are not accepted: a token may not take backups at all
    (lib.api_tokens.token_refusal).
    """
    import base64
    import binascii

    from django.contrib.auth import authenticate

    header = request.headers.get('Authorization', '')
    scheme, _, encoded = header.partition(' ')
    if scheme.lower() != 'basic' or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(':')
    if not sep:
        return None
    user = authenticate(request, username=username, password=password)
    return user if user is not None and user.is_active else None


def _download_allowed(request: HttpRequest) -> bool:
    from anthias_server.settings import settings

    if not settings['auth_backend']:
        # Authentication off: the whole device is open by contract.
        return True
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return True
    return _basic_auth_user(request) is not None


def _is_backup_archive(target: str) -> bool:
    return target.endswith('.tar.gz')


@require_GET
@require_client_in(*RFC1918_CIDRS)
def static_with_mime(request: HttpRequest, filename: str) -> HttpResponseBase:
    """Files the server stages for download: today, backup archives.

    A backup holds the Django secret key, the operator's password hash
    and the API-token table, so the RFC1918 gate alone (which admits
    the whole LAN) is not enough: the caller must be signed in, or
    send the operator's Basic credentials. Archives are also only kept
    for BACKUP_ARCHIVE_TTL_S (see lib.backup_helper).
    """
    from anthias_server.lib.backup_helper import archive_expired

    if not _download_allowed(request):
        response = HttpResponse('Authentication required.', status=401)
        response['WWW-Authenticate'] = 'Basic realm="Anthias"'
        return response
    base = os.path.realpath(STATIC_FILES_ROOT) + os.sep
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base):
        raise Http404
    if _is_backup_archive(target) and archive_expired(target):
        raise Http404
    requested_mime = request.GET.get('mime')
    if requested_mime in STATIC_WITH_MIME_ALLOWED_TYPES:
        content_type = requested_mime
    else:
        content_type = (
            mimetypes.guess_type(target)[0] or 'application/octet-stream'
        )
    try:
        return _no_store(
            FileResponse(open(target, 'rb'), content_type=content_type)
        )
    except (FileNotFoundError, IsADirectoryError):
        raise Http404


def _no_store(response: FileResponse) -> FileResponse:
    response['Cache-Control'] = 'no-store'
    return response
