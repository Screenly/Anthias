"""CSRF middleware that ignores Origin scheme on same-host POSTs.

Django 4.x+ rejects an unsafe request when its ``Origin`` header
disagrees with the request's own scheme://host. That breaks Anthias
deployments where a TLS-terminating proxy (a Caddy sidecar, a
Cloudflare Tunnel, Tailscale Serve, a home-router HTTPS rewrite, …)
hands plain HTTP to anthias-server without us also setting
``FORWARDED_ALLOW_IPS`` so uvicorn would honour ``X-Forwarded-Proto``.
The browser sees ``https://device.example``, the server sees plain
HTTP, and the default check compares ``http://device.example``
against ``https://device.example`` and 403s every form submit. The
same shape shows up after the operator disables a previously enabled
TLS proxy: the browser's HSTS / HTTPS-First cache keeps sending
``Origin: https://…`` for a while.

We don't know the operator's hostname or scheme at build time
(``CSRF_TRUSTED_ORIGINS`` only supports subdomain wildcards like
``https://*.example.com``, not the bare-scheme ``https://*`` that
would cover this), and ``ALLOWED_HOSTS`` defaults to ``*`` for the
same reason — Anthias is a LAN signage device reached by IP, mDNS,
or whatever DNS entry the operator wires up.

So: accept an Origin whose **host** matches ``request.get_host()``
even when the schemes disagree, and tolerate port drift *only* when
at least one side's port is the default for its scheme (so
``Host: device.example:443`` + ``Origin: https://device.example``
passes, but ``Origin: http://device:8080`` posting to
``Host: device:8000`` — two distinct web origins — still 403s). A
real cross-site forgery still fails: the browser sets ``Origin``
from the attacker's page, and attacker.example doesn't match the
device's host. The masked-token check still runs on top, so a
same-host Origin alone isn't enough; the POST must also carry a
token that hashes against the cookie secret, which only a page
Anthias actually rendered can produce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.core.exceptions import DisallowedHost
from django.middleware.csrf import CsrfViewMiddleware

if TYPE_CHECKING:
    from django.http import HttpRequest


_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _split_origin(
    value: str, *, default_scheme: str
) -> tuple[str, int] | None:
    """Return ``(hostname, port)`` for a URL or bare ``Host`` value.

    ``default_scheme`` is the scheme to fall back to when ``value`` is
    a bare ``Host`` header (no scheme of its own) — typically
    ``request.scheme``. Port is resolved to the scheme's default when
    the value omits it, so ``Origin: https://device.example`` and
    ``Origin: https://device.example:443`` collapse to the same
    ``(host, 443)`` pair. ``None`` means the input didn't parse.
    """
    scheme: str
    if '://' in value:
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        scheme = parts.scheme.lower() or default_scheme
        netloc = parts
    else:
        scheme = default_scheme
        try:
            netloc = urlsplit(f'//{value}')
        except ValueError:
            return None

    hostname = netloc.hostname
    if not hostname:
        return None
    try:
        explicit_port = netloc.port
    except ValueError:
        return None
    port = (
        explicit_port
        if explicit_port is not None
        else _DEFAULT_PORTS.get(scheme)
    )
    if port is None:
        return None
    return hostname.lower(), port


class SameHostOriginCsrfMiddleware(CsrfViewMiddleware):
    """``CsrfViewMiddleware`` with a scheme-agnostic same-host fallback.

    Defers to the stock check first so wildcards in
    ``CSRF_TRUSTED_ORIGINS`` and the stock scheme-strict equality
    keep working unchanged. Only when the stock check would 403 do
    we look at whether the Origin's host equals ``request.get_host()``
    and pass on that basis.
    """

    # ``_origin_verified`` is a private hook on ``CsrfViewMiddleware``
    # that django-stubs doesn't model, so the ``super()`` call needs a
    # ``type: ignore`` and the final comparison needs ``bool(...)`` to
    # satisfy ``--strict``'s ``no-any-return``. The runtime override
    # itself is well-defined — Django's CSRF middleware has called
    # this hook since 4.0.
    def _origin_verified(self, request: 'HttpRequest') -> bool:
        if super()._origin_verified(request):  # type: ignore[misc]
            return True

        request_origin = request.META.get('HTTP_ORIGIN')
        if not request_origin:
            return False

        try:
            request_host = request.get_host()
        except DisallowedHost:
            return False

        request_scheme = (request.scheme or 'http').lower()
        origin = _split_origin(request_origin, default_scheme=request_scheme)
        target = _split_origin(request_host, default_scheme=request_scheme)
        if origin is None or target is None:
            return False

        origin_host, origin_port = origin
        target_host, target_port = target
        if origin_host != target_host:
            return False

        if origin_port == target_port:
            return True

        # Different ports are accepted only when at least one side is
        # already at the scheme's default — that's the proxy/HSTS
        # shape (``Host: device:443`` + ``Origin: https://device``)
        # the fallback exists for. Two explicit non-default ports
        # mean genuinely different web origins and stay rejected.
        return origin_port in _DEFAULT_PORTS.values() or (
            target_port in _DEFAULT_PORTS.values()
        )
