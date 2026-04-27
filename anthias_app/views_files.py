import ipaddress
import mimetypes
import os
from functools import wraps
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponseForbidden
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
HOTSPOT_FILE = Path('/data/hotspot/hotspot.html')
INITIALIZED_FLAG = Path('/data/.anthias/initialized')

DOCKER_BRIDGE_CIDR = ipaddress.ip_network('172.16.0.0/12')  # NOSONAR
RFC1918_CIDRS = (
    ipaddress.ip_network('10.0.0.0/8'),  # NOSONAR
    ipaddress.ip_network('172.16.0.0/12'),  # NOSONAR
    ipaddress.ip_network('192.168.0.0/16'),  # NOSONAR
)


def _client_ip(request):
    return ipaddress.ip_address(request.META.get('REMOTE_ADDR', ''))


def require_client_in(*cidrs):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
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
def anthias_assets(request, filename):
    base = os.path.realpath(ANTHIAS_ASSETS_ROOT) + os.sep
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base):
        raise Http404
    if not os.path.isfile(target):
        raise Http404
    return FileResponse(open(target, 'rb'))


@require_GET
@require_client_in(*RFC1918_CIDRS)
def static_with_mime(request, filename):
    base = os.path.realpath(STATIC_FILES_ROOT) + os.sep
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base):
        raise Http404
    if not os.path.isfile(target):
        raise Http404
    content_type = request.GET.get('mime') or (
        mimetypes.guess_type(target)[0] or 'application/octet-stream'
    )
    return FileResponse(open(target, 'rb'), content_type=content_type)


@require_GET
@require_client_in(DOCKER_BRIDGE_CIDR)
def hotspot(request, path=''):
    if INITIALIZED_FLAG.exists() or not HOTSPOT_FILE.is_file():
        raise Http404
    return FileResponse(HOTSPOT_FILE.open('rb'), content_type='text/html')
