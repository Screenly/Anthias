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
even when the schemes disagree. A real cross-site forgery still
fails — the browser sets ``Origin`` from the attacker's page, and
attacker.example doesn't match the device's host. The masked-token
check still runs on top, so a same-host Origin alone isn't enough;
the POST must also carry a token that hashes against the cookie
secret, which only a page Anthias actually rendered can produce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from django.core.exceptions import DisallowedHost
from django.middleware.csrf import CsrfViewMiddleware

if TYPE_CHECKING:
    from django.http import HttpRequest


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
            origin_netloc = urlparse(request_origin).netloc
        except ValueError:
            return False
        if not origin_netloc:
            return False

        try:
            request_host = request.get_host()
        except DisallowedHost:
            return False

        return bool(origin_netloc.lower() == request_host.lower())
