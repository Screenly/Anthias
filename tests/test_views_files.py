import ipaddress
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import pytest
from django.http import Http404, HttpRequest, HttpResponseBase
from django.test import RequestFactory

from anthias_app import views_files
from anthias_app.views_files import (
    DOCKER_BRIDGE_CIDR,
    RFC1918_CIDRS,
    _client_ip,
    require_client_in,
)

# Standard private/public IP literals — Sonar S1313 suppressed once.
DOCKER_BRIDGE_IP = '172.18.0.1'  # NOSONAR
LAN_IP_10 = '10.0.0.5'  # NOSONAR
LAN_IP_192_ALLOWED = '192.168.1.10'  # NOSONAR
LAN_IP_192_BLOCKED = '192.168.1.50'  # NOSONAR
PUBLIC_IP = '8.8.8.8'  # NOSONAR


@pytest.fixture
def factory() -> RequestFactory:
    return RequestFactory()


# ---------------------------------------------------------------------------
# _client_ip
# ---------------------------------------------------------------------------


def test_client_ip_reads_remote_addr(factory: RequestFactory) -> None:
    request = factory.get('/', REMOTE_ADDR=LAN_IP_10)
    assert _client_ip(request) == ipaddress.ip_address(LAN_IP_10)


def test_client_ip_handles_ipv6(factory: RequestFactory) -> None:
    request = factory.get('/', REMOTE_ADDR='::1')
    assert _client_ip(request) == ipaddress.ip_address('::1')


def test_client_ip_raises_for_malformed(factory: RequestFactory) -> None:
    request = factory.get('/', REMOTE_ADDR='not-an-ip')
    with pytest.raises(ValueError):
        _client_ip(request)


def test_client_ip_raises_for_missing(factory: RequestFactory) -> None:
    request = HttpRequest()
    # No REMOTE_ADDR in META → '' → ValueError
    with pytest.raises(ValueError):
        _client_ip(request)


# ---------------------------------------------------------------------------
# require_client_in decorator
# ---------------------------------------------------------------------------


def test_require_client_in_allows_match(factory: RequestFactory) -> None:
    @require_client_in(DOCKER_BRIDGE_CIDR)
    def view(request: HttpRequest) -> Any:
        from django.http import HttpResponse

        return HttpResponse('ok')

    request = factory.get('/', REMOTE_ADDR=DOCKER_BRIDGE_IP)
    response = view(request)
    assert response.status_code == 200


def test_require_client_in_rejects_outside_cidr(
    factory: RequestFactory,
) -> None:
    @require_client_in(DOCKER_BRIDGE_CIDR)
    def view(request: HttpRequest) -> Any:
        from django.http import HttpResponse

        return HttpResponse('ok')

    response = view(factory.get('/', REMOTE_ADDR=PUBLIC_IP))
    assert response.status_code == 403


def test_require_client_in_rejects_malformed_remote_addr(
    factory: RequestFactory,
) -> None:
    @require_client_in(DOCKER_BRIDGE_CIDR)
    def view(request: HttpRequest) -> Any:
        from django.http import HttpResponse

        return HttpResponse('ok')

    response = view(factory.get('/', REMOTE_ADDR='garbage'))
    assert response.status_code == 403


def test_require_client_in_with_multiple_cidrs(
    factory: RequestFactory,
) -> None:
    @require_client_in(*RFC1918_CIDRS)
    def view(request: HttpRequest) -> Any:
        from django.http import HttpResponse

        return HttpResponse('ok')

    for ip in (LAN_IP_10, DOCKER_BRIDGE_IP, LAN_IP_192_ALLOWED):
        assert view(factory.get('/', REMOTE_ADDR=ip)).status_code == 200
    assert view(factory.get('/', REMOTE_ADDR=PUBLIC_IP)).status_code == 403


# ---------------------------------------------------------------------------
# anthias_assets view
# ---------------------------------------------------------------------------


@pytest.fixture
def assets_root() -> Iterator[Path]:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    (root / 'hello.txt').write_text('hello')
    with mock.patch.object(views_files, 'ANTHIAS_ASSETS_ROOT', root):
        yield root
    tmp.cleanup()


def _call_anthias_assets(
    factory: RequestFactory,
    filename: str,
    remote_addr: str,
) -> HttpResponseBase:
    request = factory.get(
        f'/anthias_assets/{filename}', REMOTE_ADDR=remote_addr
    )
    return views_files.anthias_assets(request, filename=filename)


def test_anthias_assets_serves_file_for_docker_bridge_client(
    factory: RequestFactory, assets_root: Path
) -> None:
    response = _call_anthias_assets(factory, 'hello.txt', DOCKER_BRIDGE_IP)
    assert response.status_code == 200


def test_anthias_assets_blocks_public_client(
    factory: RequestFactory, assets_root: Path
) -> None:
    response = _call_anthias_assets(factory, 'hello.txt', PUBLIC_IP)
    assert response.status_code == 403


def test_anthias_assets_blocks_192_lan_client(
    factory: RequestFactory, assets_root: Path
) -> None:
    response = _call_anthias_assets(factory, 'hello.txt', LAN_IP_192_BLOCKED)
    assert response.status_code == 403


def test_anthias_assets_traversal_blocked(
    factory: RequestFactory, assets_root: Path
) -> None:
    request = factory.get('/anthias_assets/foo', REMOTE_ADDR=DOCKER_BRIDGE_IP)
    with pytest.raises(Http404):
        views_files.anthias_assets(request, filename='../../../etc/passwd')


def test_anthias_assets_missing_file(
    factory: RequestFactory, assets_root: Path
) -> None:
    request = factory.get(
        '/anthias_assets/missing.txt', REMOTE_ADDR=DOCKER_BRIDGE_IP
    )
    with pytest.raises(Http404):
        views_files.anthias_assets(request, filename='missing.txt')


def test_anthias_assets_symlink_escape_blocked(
    factory: RequestFactory, assets_root: Path
) -> None:
    with TemporaryDirectory() as outside_dir:
        outside = Path(outside_dir) / 'secret.txt'
        outside.write_text('secret')
        (assets_root / 'link.txt').symlink_to(outside)
        request = factory.get(
            '/anthias_assets/link.txt', REMOTE_ADDR=DOCKER_BRIDGE_IP
        )
        with pytest.raises(Http404):
            views_files.anthias_assets(request, filename='link.txt')


def test_anthias_assets_directory_request_404(
    factory: RequestFactory, assets_root: Path
) -> None:
    (assets_root / 'subdir').mkdir()
    request = factory.get(
        '/anthias_assets/subdir', REMOTE_ADDR=DOCKER_BRIDGE_IP
    )
    with pytest.raises(Http404):
        views_files.anthias_assets(request, filename='subdir')


# ---------------------------------------------------------------------------
# static_with_mime view
# ---------------------------------------------------------------------------


@pytest.fixture
def static_root() -> Iterator[Path]:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    (root / 'app.css').write_text('body{}')
    (root / 'archive.tgz').write_bytes(b'\x1f\x8b...')
    with mock.patch.object(views_files, 'STATIC_FILES_ROOT', root):
        yield root
    tmp.cleanup()


def _call_static(
    factory: RequestFactory,
    filename: str,
    remote_addr: str,
    **extra: Any,
) -> HttpResponseBase:
    request = factory.get(
        f'/static_with_mime/{filename}', REMOTE_ADDR=remote_addr, **extra
    )
    return views_files.static_with_mime(request, filename=filename)


@pytest.mark.parametrize(
    'remote_addr',
    [LAN_IP_10, DOCKER_BRIDGE_IP, LAN_IP_192_ALLOWED],
)
def test_static_with_mime_allows_rfc1918_clients(
    factory: RequestFactory, static_root: Path, remote_addr: str
) -> None:
    response = _call_static(factory, 'app.css', remote_addr)
    assert response.status_code == 200


def test_static_with_mime_blocks_public_client(
    factory: RequestFactory, static_root: Path
) -> None:
    response = _call_static(factory, 'app.css', PUBLIC_IP)
    assert response.status_code == 403


def test_static_with_mime_query_overrides_to_allowed_type(
    factory: RequestFactory, static_root: Path
) -> None:
    request = factory.get(
        '/static_with_mime/archive.tgz',
        data={'mime': 'application/x-tgz'},
        REMOTE_ADDR=LAN_IP_10,
    )
    response = views_files.static_with_mime(request, filename='archive.tgz')
    assert response['Content-Type'] == 'application/x-tgz'


def test_static_with_mime_disallowed_type_falls_back_to_guess(
    factory: RequestFactory, static_root: Path
) -> None:
    """A disallowed ?mime= must not be honoured (XSS prevention)."""
    request = factory.get(
        '/static_with_mime/app.css',
        data={'mime': 'text/html'},
        REMOTE_ADDR=LAN_IP_10,
    )
    response = views_files.static_with_mime(request, filename='app.css')
    assert response['Content-Type'] == 'text/css'


def test_static_with_mime_default_octet_stream_for_unknown_extension(
    factory: RequestFactory, static_root: Path
) -> None:
    (static_root / 'mystery.unknownextxyz').write_text('???')
    request = factory.get(
        '/static_with_mime/mystery.unknownextxyz', REMOTE_ADDR=LAN_IP_10
    )
    response = views_files.static_with_mime(
        request, filename='mystery.unknownextxyz'
    )
    assert response['Content-Type'] == 'application/octet-stream'


def test_static_with_mime_traversal_blocked(
    factory: RequestFactory, static_root: Path
) -> None:
    request = factory.get('/static_with_mime/x', REMOTE_ADDR=LAN_IP_10)
    with pytest.raises(Http404):
        views_files.static_with_mime(request, filename='../../../etc/passwd')


def test_static_with_mime_missing_file(
    factory: RequestFactory, static_root: Path
) -> None:
    request = factory.get(
        '/static_with_mime/missing.css', REMOTE_ADDR=LAN_IP_10
    )
    with pytest.raises(Http404):
        views_files.static_with_mime(request, filename='missing.css')


def test_static_with_mime_directory_request_404(
    factory: RequestFactory, static_root: Path
) -> None:
    (static_root / 'subdir').mkdir()
    request = factory.get('/static_with_mime/subdir', REMOTE_ADDR=LAN_IP_10)
    with pytest.raises(Http404):
        views_files.static_with_mime(request, filename='subdir')
