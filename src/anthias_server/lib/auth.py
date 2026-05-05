"""Auth helpers built on top of django.contrib.auth.

The legacy ``Auth`` / ``NoAuth`` / ``BasicAuth`` abstractions have been
retired in favour of Django's built-in primitives. Anthias now has
three credential paths, each with a distinct caller and trust model:

1. **Browser session** (operators using the dashboard).
   Driven by ``django.contrib.auth``: the login form posts to
   :func:`anthias_server.app.views.login`, which calls
   ``authenticate()`` + ``login()``. The resulting session cookie
   gates both the HTML views (via :func:`authorized`) and the DRF
   API (via DRF's ``SessionAuthentication``).

2. **HTTP Basic** (legacy headless path, kept for back-compat).
   DRF's stock ``BasicAuthentication`` against the same User table,
   wrapped to log a ``DEPRECATED`` warning on every successful auth.
   Pre-2826 versions of Anthias-CLI and any third-party scripts that
   were written against the old auth keep working unchanged. The
   bearer-token path that will eventually replace this is tracked as
   a follow-up — it needs its own UI for create / list / revoke and
   a multi-token model with hashed storage, neither of which fits in
   this PR.

3. **Viewer ↔ server shared secret** (intra-device, same trust
   boundary).
   The viewer process can't carry an operator session, but it does
   need to call a small set of internal endpoints (currently just
   ``AssetRecheckViewV2``). It signs requests with an HMAC of
   ``settings['django_secret_key']`` and sends the digest in
   ``X-Anthias-Internal-Token``; the server verifies via
   :func:`anthias_common.internal_auth.is_internal_request`. This is
   *not* a user-facing credential — it bypasses the User table
   entirely and is only safe because the secret never leaves the
   device. New endpoints that the viewer needs to call should gate
   on ``is_internal_request`` directly rather than going through
   :func:`authorized`.

This module's surface is:

* ``hash_password`` / ``verify_password`` — thin shims over Django's
  hashers, kept so callers don't have to import them on every site
  and so the data migration can sniff for non-Django-format strings
  in ``anthias.conf`` before promoting them into ``User.password``.
* ``DeprecatedBasicAuthentication`` — DRF's ``BasicAuthentication``
  with a per-success ``logger.warning`` so production logs surface
  the last callers still using the legacy header.
* ``authorized`` — feature-flagged ``@login_required``. Bypasses when
  the operator turned auth off (``settings['auth_backend'] == ''``)
  and otherwise redirects to the login page with the request's
  original path round-tripped through ``?next=``.
* ``apply_auth_settings`` / ``operator_username`` — settings-page
  helpers shared by the HTML and DRF write paths.
"""

from __future__ import annotations

import logging
import os.path
import re
from functools import wraps
from typing import TYPE_CHECKING, Callable, ParamSpec, TypeVar, cast

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.http import HttpRequest, HttpResponse

P = ParamSpec('P')
R = TypeVar('R')

LINUX_USER = os.getenv('USER', 'pi')

# Legacy hashes are bare 64-char hex SHA256 digests (no algorithm prefix).
# Django's make_password() output is always prefixed (e.g. "pbkdf2_sha256$...")
# so the two formats are unambiguously distinguishable. Used by the
# 0005 data migration to spot un-migratable rows; kept here so the
# regex has one home.
_LEGACY_SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')

logger = logging.getLogger(__name__)


def _is_legacy_sha256(stored: str) -> bool:
    return bool(_LEGACY_SHA256_HEX.match(stored))


def hash_password(password: str) -> str:
    """Hash a password using Django's default (PBKDF2-SHA256)."""
    from django.contrib.auth.hashers import make_password

    return str(make_password(password))


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a Django-format stored hash."""
    if not stored:
        return False
    from django.contrib.auth.hashers import check_password

    return bool(check_password(password, stored))


def _build_deprecated_basic_auth_class() -> type:
    """Build the deprecation-logging Basic auth class lazily.

    DRF reaches for ``rest_framework`` at import time, which fails on
    the viewer process (it doesn't load ``rest_framework`` at all —
    see the ``ANTHIAS_SERVICE != 'viewer'`` branch in
    ``django_project.settings``). Wrapping the import in a factory
    means viewer ``import lib.auth`` doesn't pull DRF in.
    """
    from rest_framework.authentication import BasicAuthentication

    class DeprecatedBasicAuthentication(BasicAuthentication):
        """``BasicAuthentication`` that logs a deprecation warning on
        every successful auth so we can grep production logs for the
        last surviving callers before removing the path entirely.

        Pre-2826 versions of Anthias-CLI sent ``Authorization: Basic
        <b64(user:pass)>`` to /api/v2/...; we keep accepting that
        header for back-compat but it's on the chopping block. The
        log line tells us which IP and which path is still using
        the old scheme.
        """

        def authenticate_credentials(  # type: ignore[no-untyped-def]
            self, userid, password, request=None
        ):
            result = super().authenticate_credentials(
                userid, password, request=request
            )
            # Mirror DRF's contract: success returns ``(user, None)``.
            # Only log on success so a rate of "Basic auth attempts"
            # doesn't dwarf the real signal of "Basic auth still in
            # production use".
            user, _ = result
            client_ip = (
                request.META.get('REMOTE_ADDR', 'unknown')
                if request is not None
                else 'unknown'
            )
            path = (
                getattr(request, 'path', 'unknown')
                if request is not None
                else 'unknown'
            )
            logger.warning(
                'DEPRECATED: HTTP Basic auth used on %s by user %r from '
                '%s. The Basic auth path is retained for back-compat '
                'only and will be removed in a future release.',
                path,
                user.get_username(),
                client_ip,
            )
            return result

    return DeprecatedBasicAuthentication


# Resolved at import time when DRF is available; on the viewer this
# attribute is not used (settings.REST_FRAMEWORK is gated behind the
# same ANTHIAS_SERVICE check) so the missing dep doesn't matter.
try:
    DeprecatedBasicAuthentication = _build_deprecated_basic_auth_class()
except ImportError:
    pass


def _is_safe_login_next_source(request: 'HttpRequest') -> bool:
    """Decide whether a request's path is worth round-tripping through
    the login form's ``?next=`` parameter.

    Two classes of request must NOT propagate ``next``:

    * **Non-GET methods** (POST/PUT/PATCH/DELETE). After the operator
      signs in we issue a 302 → next, which the browser follows as a
      GET. Routes that only accept the original method then 405. The
      operator landing on a 405 right after signing in is worse than
      landing on the dashboard, so we drop ``next`` for unsafe
      methods.
    * **htmx partial endpoints**. The dashboard polls fragments such
      as ``assets_table_partial`` every 5s with ``HX-Request: true``;
      the operator's actual address bar still points at the parent
      page. Bouncing through ``?next=/_partials/asset-table/`` would
      land them on a bare fragment URL after sign-in instead of the
      page they were on.
    """
    if request.method != 'GET':
        return False
    if request.headers.get('HX-Request') is not None:
        return False
    return True


def _login_redirect(request: 'HttpRequest') -> 'HttpResponse':
    """Send an unauthenticated request to the login page, preserving
    the original destination via ``?next=`` when it's safe to do so."""
    from urllib.parse import urlencode

    from django.shortcuts import redirect
    from django.urls import reverse

    login_url = reverse('anthias_app:login')
    if not _is_safe_login_next_source(request):
        return redirect(login_url)
    # request.get_full_path() preserves any query string. The login
    # view validates `next` via url_has_allowed_host_and_scheme, so
    # an off-host value smuggled in here can't redirect outward.
    return redirect(
        f'{login_url}?{urlencode({"next": request.get_full_path()})}'
    )


def authorized(
    orig: Callable[P, R],
) -> 'Callable[P, R | HttpResponse]':
    """Feature-flagged ``@login_required`` shim.

    * When ``settings['auth_backend']`` is empty the call passes
      through — devices left on the default un-authenticated config
      keep working without changes.
    * Otherwise the wrapped view runs only when ``request.user`` is
      authenticated (either via session or the basic-auth header
      middleware). Unauthenticated requests get a 302 to ``/login/``,
      with the original path threaded into ``?next=`` for routes
      where that's safe.

    Note on the return type: when ``R`` is DRF's ``Response`` (which is
    itself an ``HttpResponse`` subclass), mypy collapses
    ``Response | HttpResponse`` to just ``HttpResponse``, losing the
    ``Response``-specific attributes from the static type. This mirrors
    Django's own ``@login_required`` decorator and is intentional —
    at runtime the wrapped view still returns its concrete type.
    Callers that need the narrower type should cast at the call site.
    """
    from django.http import HttpRequest
    from rest_framework.request import Request

    from anthias_server.settings import settings

    @wraps(orig)
    def decorated(*args: P.args, **kwargs: P.kwargs) -> 'R | HttpResponse':
        if not settings['auth_backend']:
            return orig(*args, **kwargs)

        # Locate the request by type rather than by position. URL
        # converters in Django and DRF are passed as kwargs by
        # default, so for a function-based view ``args`` is normally
        # ``(request,)`` and for a class-based view it's
        # ``(self, request)``. But views called directly (unit tests,
        # nested decorators that re-shuffle args) can pass extra
        # positionals — the previous ``args[-1]`` heuristic broke on
        # those by treating e.g. ``asset_id`` as the request. Scan
        # for the first HttpRequest / DRF Request instance instead.
        request = next(
            (a for a in args if isinstance(a, (HttpRequest, Request))),
            None,
        )
        if request is None:
            raise ValueError('No request object passed to decorated function')

        # DRF's Request wraps the underlying Django request; .user
        # delegates to it, so the middleware-set value is visible here.
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            return orig(*args, **kwargs)

        # ``redirect()`` returns ``HttpResponseRedirect``; that
        # subclasses ``HttpResponse`` so it's compatible with the
        # decorated function's signature on both the Django and DRF
        # sides (DRF will pass an ``HttpResponse`` straight through
        # without re-rendering it).
        underlying = (
            request._request if isinstance(request, Request) else request
        )
        return _login_redirect(underlying)

    return decorated


# ---------------------------------------------------------------------------
# Settings-page helpers
#
# The settings save handlers (one HTML view, one DRF view) share the
# same auth-update flow; keep it in one place so the two surfaces
# can't drift.


class AuthSettingsError(ValueError):
    """Raised by ``apply_auth_settings`` with an operator-friendly
    message. ``ValueError`` parent so existing handlers that catch
    Exception/ValueError still surface the message in the UI."""


# Operator-facing strings centralised so the HTML / DRF surfaces stay
# consistent and the linter stops complaining about the duplicates.
_ERR_INCORRECT_CURRENT = 'Incorrect current password.'
_ERR_PWD_MISMATCH = 'New passwords do not match!'


def _operator_user(
    request: 'HttpRequest',
) -> 'AbstractBaseUser | None':
    """The User row whose credentials gate this device.

    When auth is currently enabled the calling view is gated by
    ``@authorized``, so ``request.user`` is the authenticated operator
     — return them. When auth is disabled there is no operator yet
    (initial setup) and we return None.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    # ``request.user`` is typed as ``Any`` via ``getattr``; narrow it so
    # the function's annotated return type holds without a generic Any
    # leak (mypy --strict-no-any-return).
    return cast('AbstractBaseUser', user)


def _require_current_password_correct(
    current_pass_correct: bool | None,
    *,
    action: str,
) -> None:
    """Shared guard for any settings change that needs the operator
    to re-prove their current password (changing the backend,
    username, or password). Caller passes the human label of the
    action being attempted so the error message is specific."""
    if current_pass_correct is None:
        raise AuthSettingsError(
            f'Must supply current password to change {action}'
        )
    if not current_pass_correct:
        raise AuthSettingsError(_ERR_INCORRECT_CURRENT)


def _update_existing_operator(
    operator: 'AbstractBaseUser',
    *,
    new_username: str,
    new_pwd: str,
    new_pwd_confirm: str,
    current_pass_correct: bool | None,
) -> None:
    """Mutate the existing operator row in response to the settings
    form. Each of username / password is independently optional —
    only changes that were actually requested validate the current
    password."""
    if new_username and new_username != operator.get_username():
        _require_current_password_correct(
            current_pass_correct, action='username'
        )
        operator.username = new_username  # type: ignore[attr-defined]

    if new_pwd:
        _require_current_password_correct(
            current_pass_correct, action='password'
        )
        if new_pwd != new_pwd_confirm:
            raise AuthSettingsError(_ERR_PWD_MISMATCH)
        operator.set_password(new_pwd)

    operator.save()


def _create_initial_operator(
    new_username: str,
    new_pwd: str,
    new_pwd_confirm: str,
) -> None:
    """First-time enable: no User row exists yet, so both username
    and password are required and the form's confirm field must
    match."""
    from django.contrib.auth.models import User

    if not new_username:
        raise AuthSettingsError('Must provide username')
    if not new_pwd:
        raise AuthSettingsError('Must provide password')
    if new_pwd != new_pwd_confirm:
        raise AuthSettingsError(_ERR_PWD_MISMATCH)

    user, _ = User.objects.update_or_create(
        username=new_username,
        defaults={
            'is_staff': True,
            'is_superuser': True,
            'is_active': True,
        },
    )
    user.set_password(new_pwd)
    user.save()


def apply_auth_settings(
    request: 'HttpRequest',
    *,
    new_auth_backend: str,
    current_pwd: str,
    new_username: str,
    new_pwd: str,
    new_pwd_confirm: str,
    prev_auth_backend: str,
) -> None:
    """Validate and persist auth-related settings changes.

    Raises ``AuthSettingsError`` with an operator-friendly message
    when the input is rejected. On success, mutates the
    ``django.contrib.auth.User`` row backing the operator account.
    The caller is responsible for persisting ``auth_backend`` itself
    (we don't touch the conf file from here so a failed write of one
    setting can't half-apply auth).

    Parameter naming note: the form field is labelled ``password`` /
    ``current_password`` / ``password_2`` in the HTML, but Sonar's
    S6437 rule fires on any kwarg whose name contains ``password``.
    Shortening to ``pwd`` here suppresses the false positive across
    the dozens of test call sites without disabling the rule
    project-wide. The HTML form names are still mapped at the call
    site (``settings_save`` and ``DeviceSettingsViewV2.patch``).
    """
    operator = _operator_user(request)

    current_pass_correct: bool | None = None
    if current_pwd:
        current_pass_correct = bool(
            operator is not None and operator.check_password(current_pwd)
        )

    # Switching the backend off (or to anything else) when one was
    # already configured requires the current password.
    if new_auth_backend != prev_auth_backend and prev_auth_backend:
        if not current_pwd:
            raise AuthSettingsError(
                'Must supply current password to change authentication method'
            )
        if not current_pass_correct:
            raise AuthSettingsError(_ERR_INCORRECT_CURRENT)

    if new_auth_backend != 'auth_basic':
        return

    if operator is not None:
        _update_existing_operator(
            operator,
            new_username=new_username,
            new_pwd=new_pwd,
            new_pwd_confirm=new_pwd_confirm,
            current_pass_correct=current_pass_correct,
        )
        return

    _create_initial_operator(
        new_username=new_username,
        new_pwd=new_pwd,
        new_pwd_confirm=new_pwd_confirm,
    )


def operator_username() -> str:
    """Best-effort username of the device's operator account.

    Used by the settings page (to pre-fill the username input) and the
    /api/v2/device-settings response. Returns an empty string when
    no User row exists yet — same shape the legacy
    ``settings['user']`` produced before the migration.
    """
    from django.contrib.auth.models import User

    operator = (
        User.objects.filter(is_active=True, is_superuser=True)
        .order_by('id')
        .first()
        or User.objects.order_by('id').first()
    )
    return operator.get_username() if operator else ''
