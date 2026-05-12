import ipaddress
import mimetypes
import os
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponseForbidden,
)
from django.http.response import HttpResponseBase
from django.views.decorators.http import require_GET

# Defense-in-depth, not the real perimeter:
#   * In the default no-SSL install, anthias-server is published as
#     `80:8080`, so requests arrive with REMOTE_ADDR set to the docker
#     bridge gateway (172.x) — that IP falls inside DOCKER_BRIDGE_CIDR,
#     so LAN clients are not actually excluded. Mirrors the original
#     nginx posture; trying to plug the gap with auth would be theatre
#     anyway since plain-HTTP credentials are sniffable on the LAN.
#   * With `bin/enable_ssl.sh` the Caddy sidecar terminates TLS and
#     rewrites X-Forwarded-For with the real client IP; uvicorn honours
#     that header (FORWARDED_ALLOW_IPS=* in the override) and the check
#     below sees the LAN IP, so the bypass is closed for SSL deployments.
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
    try:
        return FileResponse(open(target, 'rb'))
    except (FileNotFoundError, IsADirectoryError):
        raise Http404


@require_GET
@require_client_in(*RFC1918_CIDRS)
def static_with_mime(request: HttpRequest, filename: str) -> HttpResponseBase:
    base = os.path.realpath(STATIC_FILES_ROOT) + os.sep
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base):
        raise Http404
    requested_mime = request.GET.get('mime')
    if requested_mime in STATIC_WITH_MIME_ALLOWED_TYPES:
        content_type = requested_mime
    else:
        content_type = (
            mimetypes.guess_type(target)[0] or 'application/octet-stream'
        )
    try:
        return FileResponse(open(target, 'rb'), content_type=content_type)
    except (FileNotFoundError, IsADirectoryError):
        raise Http404
