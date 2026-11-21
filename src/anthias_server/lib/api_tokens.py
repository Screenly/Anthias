"""Issue, verify and revoke API bearer tokens.

Tokens are the headless credential for the REST API: an integration
sends ``Authorization: Bearer <token>`` instead of the operator's
username and password. Each token is a random secret shown to the
operator once; the database keeps only its SHA-256 digest and a short
display prefix (see :class:`anthias_server.app.models.ApiToken`).

Tokens only mean something while authentication is enabled. With
``settings['auth_backend'] == ''`` the API is open by contract, so a
token would add nothing but a false sense of security:

* :func:`create_token` refuses to issue one;
* :func:`authenticate_token` never matches one, so the DRF
  authenticator ignores the header (the request still succeeds,
  because the API is open, but it is anonymous and the token's
  ``last_used_at`` is not touched);
* tokens created earlier are kept, are listed as inactive, and can
  still be revoked. They start working again only if authentication
  is turned back on.

Rotating the operator's password does not revoke tokens. They are
separate credentials, revoked one by one from the settings page.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser, User
    from django.db.models import QuerySet

    from anthias_server.app.models import ApiToken

TOKEN_PREFIX = 'anthias_'
_TOKEN_BYTES = 32
DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 4
MAX_NAME_LEN = 100

# How stale ``last_used_at`` may get before a request refreshes it.
# A polling client would otherwise turn every API call into an SQLite
# write on the SD card; minute resolution is plenty for "is this
# token still in use?".
LAST_USED_RESOLUTION = timedelta(minutes=1)


class ApiTokenError(ValueError):
    """Operator-facing reason a token could not be created."""


def tokens_enabled() -> bool:
    """Whether tokens can be issued and used right now."""
    from anthias_server.settings import settings

    return bool(settings['auth_backend'])


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def active_tokens() -> QuerySet[ApiToken]:
    from anthias_server.app.models import ApiToken

    return ApiToken.objects.filter(revoked_at__isnull=True)


def create_token(
    user: AbstractBaseUser | AnonymousUser, name: str
) -> tuple[ApiToken, str]:
    """Issue a token for ``user``.

    Returns the saved row and the plaintext token. The plaintext is
    not stored anywhere; the caller shows it once and drops it.
    """
    from anthias_server.app.models import ApiToken

    if not tokens_enabled():
        raise ApiTokenError(
            'Turn on authentication before creating API tokens. '
            'While authentication is disabled the API is open to '
            'anyone on the network, so a token would not protect it.'
        )
    if not user.is_authenticated:
        raise ApiTokenError('Sign in to create API tokens.')
    name = (name or '').strip()
    if not name:
        raise ApiTokenError('Give the token a name.')
    if len(name) > MAX_NAME_LEN:
        raise ApiTokenError(
            f'Token names can be at most {MAX_NAME_LEN} characters.'
        )

    plaintext = generate_token()
    token = ApiToken.objects.create(
        name=name,
        prefix=plaintext[:DISPLAY_PREFIX_LEN],
        token_hash=hash_token(plaintext),
        user=cast('User', user),
    )
    return token, plaintext


def revoke_token(token_id: int) -> bool:
    """Revoke a token. Returns False if no active token has that id."""
    from django.utils import timezone

    updated = (
        active_tokens().filter(pk=token_id).update(revoked_at=timezone.now())
    )
    return bool(updated)


def authenticate_token(plaintext: str) -> ApiToken | None:
    """Return the active token matching ``plaintext``, or None.

    None when authentication is disabled, when no token matches, when
    the token is revoked, or when its user is inactive.
    """
    from django.db.models import Q
    from django.utils import timezone

    from anthias_server.app.models import ApiToken

    if not plaintext or not tokens_enabled():
        return None
    token = (
        active_tokens()
        .select_related('user')
        .filter(token_hash=hash_token(plaintext), user__is_active=True)
        .first()
    )
    if token is None:
        return None

    now = timezone.now()
    last_used = token.last_used_at
    if last_used is None or now - last_used >= LAST_USED_RESOLUTION:
        updated = (
            ApiToken.objects.filter(pk=token.pk)
            .filter(
                Q(last_used_at__isnull=True)
                | Q(last_used_at__lte=now - LAST_USED_RESOLUTION)
            )
            .update(last_used_at=now)
        )
        if updated:
            token.last_used_at = now
    return token


# --- what a token may do ---------------------------------------------------
#
# A token is a headless credential, not the operator. It must not be
# able to turn itself into the operator's password or into a copy of
# the device: a backup carries the Django secret key and the password
# hash, and a restore replaces the database (and so can plant new
# credentials that outlive revoking the token). Those stay with the
# logged-in operator.
_TOKEN_DENIED_PATH_RE = re.compile(r'^/api/v[0-9.]+/(backup|recover)/?$')

# The controller's token (one that pairs a ManagementController) is
# narrower still: only the calls controller.client makes, and only
# through the :8443 machine-to-machine listener.
_CONTROLLER_ALLOWED: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r'^/api/v2/info$'), frozenset({'GET', 'HEAD'})),
    (re.compile(r'^/api/v2/pairing$'), frozenset({'GET', 'HEAD', 'DELETE'})),
    (re.compile(r'^/api/v2/assets$'), frozenset({'GET', 'HEAD', 'POST'})),
    (re.compile(r'^/api/v2/assets/order$'), frozenset({'POST'})),
    (
        re.compile(r'^/api/v2/assets/control/[^/]+$'),
        frozenset({'GET', 'HEAD'}),
    ),
    (
        re.compile(r'^/api/v2/assets/[^/]+$'),
        frozenset({'GET', 'HEAD', 'PATCH', 'PUT', 'DELETE'}),
    ),
    (re.compile(r'^/api/v2/file_asset$'), frozenset({'POST'})),
    (re.compile(r'^/api/v2/reboot$'), frozenset({'POST'})),
    (re.compile(r'^/api/v2/display/[^/]+$'), frozenset({'POST'})),
)


def is_controller_token(token: ApiToken) -> bool:
    from anthias_server.app.models import ManagementController

    return ManagementController.objects.filter(token_id=token.pk).exists()


def token_refusal(
    token: ApiToken, method: str, path: str, via_m2m: bool
) -> str:
    """Why ``token`` may not make this request, or ``''`` if it may."""
    if _TOKEN_DENIED_PATH_RE.match(path):
        return (
            'API tokens cannot create or restore backups. Sign in to the '
            'web interface to do that.'
        )
    if is_controller_token(token):
        if not via_m2m:
            return (
                "The controller's token is only accepted on the "
                'management port.'
            )
        method = method.upper()
        if not any(
            pattern.match(path) and method in methods
            for pattern, methods in _CONTROLLER_ALLOWED
        ):
            return "The controller's token does not allow this request."
    return ''
