import ipaddress
import mimetypes
import os
from functools import wraps
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponseForbidden
from django.views.decorators.http import require_GET

# Defense-in-depth, not a real perimeter: with `ports: 80:8080` the host's
# docker-bridge IP is also in 172.16/12, so LAN clients hitting the
# published port aren't excluded. Mirrors the original nginx allowlist.
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


def _safe_join(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``, rejecting traversal."""
    # os.path.commonpath is CodeQL's recognised path-injection sanitiser;
    # equivalent to Path.is_relative_to but the static analyser can prove
    # the post-check path stays under `root`.
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(os.path.join(root_real, relative))
    if os.path.commonpath([candidate_real, root_real]) != root_real:
        raise Http404
    return Path(candidate_real)


@require_GET
@require_client_in(DOCKER_BRIDGE_CIDR)
def anthias_assets(request, filename):
    target = _safe_join(ANTHIAS_ASSETS_ROOT, filename)
    if not target.is_file():
        raise Http404
    return FileResponse(target.open('rb'))


@require_GET
@require_client_in(*RFC1918_CIDRS)
def static_with_mime(request, filename):
    target = _safe_join(STATIC_FILES_ROOT, filename)
    if not target.is_file():
        raise Http404
    content_type = request.GET.get('mime') or (
        mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
    )
    return FileResponse(target.open('rb'), content_type=content_type)


@require_GET
@require_client_in(DOCKER_BRIDGE_CIDR)
def hotspot(request, path=''):
    if INITIALIZED_FLAG.exists() or not HOTSPOT_FILE.is_file():
        raise Http404
    return FileResponse(HOTSPOT_FILE.open('rb'), content_type='text/html')
