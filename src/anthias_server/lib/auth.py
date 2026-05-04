#!/usr/bin/env python
# -*- coding: utf-8 -*-

import binascii
import os.path
import re
from abc import ABCMeta, abstractmethod
from base64 import b64decode
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, ParamSpec, TypeVar

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

P = ParamSpec('P')
R = TypeVar('R')

LINUX_USER = os.getenv('USER', 'pi')

# Legacy hashes are bare 64-char hex SHA256 digests (no algorithm prefix).
# Django's make_password() output is always prefixed (e.g. "pbkdf2_sha256$...")
# so the two formats are unambiguously distinguishable.
_LEGACY_SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')


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


class Auth(metaclass=ABCMeta):
    display_name: str = ''
    name: str = ''
    config: dict[str, Any] = {}

    @abstractmethod
    def authenticate(
        self, request: 'HttpRequest | None' = None
    ) -> 'HttpResponse | None':
        """
        Let the user authenticate himself.

        :param request: the inbound request that triggered the auth
            check. Implementations that redirect to a login form use
            it to attach a ``?next=<original-path>`` so the operator
            returns to where they were after signing in. Optional —
            backends with no return-to concept (e.g. NoAuth) ignore it.
        :return: a Response which initiates authentication.
        """
        pass

    def is_authenticated(self, request: 'HttpRequest') -> bool:
        """
        See if the user is authenticated for the request.
        :return: bool
        """
        return False

    def authenticate_if_needed(
        self,
        request: 'HttpRequest',
    ) -> 'HttpResponse | None':
        """
        If the user performing the request is not authenticated, initiate
        authentication.

        :return: a Response which initiates authentication or None
        if already authenticated.
        """
        from django.http import HttpResponse

        try:
            if not self.is_authenticated(request):
                return self.authenticate(request)
        except ValueError as e:
            return HttpResponse(
                'Authorization backend is unavailable: ' + str(e), status=503
            )
        return None

    def update_settings(
        self,
        request: 'HttpRequest',
        current_pass_correct: bool | None,
    ) -> None:
        """
        Submit updated values from Settings page.
        :param current_pass_correct: the value of "Current Password" field
        or None if empty.

        :return:
        """
        pass

    @property
    def template(self) -> tuple[str, dict[str, Any]] | None:
        """
        Get HTML template and its context object to be displayed in
        the vettings page.

        :return: (template, context)
        """
        return None

    def check_password(self, password: str) -> bool:
        """
        Checks if password correct.
        :param password: str
        :return: bool
        """
        return False


class NoAuth(Auth):
    display_name = 'Disabled'
    name = ''
    config: dict[str, Any] = {}

    def is_authenticated(self, request: 'HttpRequest') -> bool:
        return True

    def authenticate(self, request: 'HttpRequest | None' = None) -> None:
        pass

    def check_password(self, password: str) -> bool:
        return True


class BasicAuth(Auth):
    display_name = 'Basic'
    name = 'auth_basic'
    config: dict[str, Any] = {'auth_basic': {'user': '', 'password': ''}}

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def _check(self, username: str, password: str) -> bool:
        """
        Check username/password combo against database.
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        return bool(
            self.settings['user'] == username and self.check_password(password)
        )

    def check_password(self, password: str) -> bool:
        return verify_password(password, self.settings['password'])

    def is_authenticated(self, request: 'HttpRequest') -> bool:
        # First check Authorization header for API requests
        authorization = request.headers.get('Authorization')
        if authorization:
            content = authorization.split(' ')
            if len(content) == 2:
                auth_type = content[0]
                auth_data = content[1]
                if auth_type == 'Basic':
                    try:
                        decoded = b64decode(auth_data).decode('utf-8')
                    except (binascii.Error, UnicodeDecodeError, ValueError):
                        # Malformed Authorization header — treat as
                        # unauthenticated rather than letting the decode
                        # error bubble up and degrade availability.
                        return False
                    # RFC 7617 allows ':' in the password portion; split
                    # only on the first ':' so passwords with colons work.
                    username, sep, password = decoded.partition(':')
                    if sep:
                        return self._check(username, password)

        # Then check session for form-based login
        session_username = request.session.get('auth_username')
        session_password = request.session.get('auth_password')
        if session_username and session_password:
            return self._check(session_username, session_password)

        return False

    @property
    def template(self) -> tuple[str, dict[str, Any]]:
        return 'auth_basic.html', {'user': self.settings['user']}

    def authenticate(
        self, request: 'HttpRequest | None' = None
    ) -> 'HttpResponse':
        from urllib.parse import urlencode

        from django.shortcuts import redirect
        from django.urls import reverse

        login_url = reverse('anthias_app:login')
        # Round-trip the operator's original destination through the
        # login form so they don't land on the dashboard after signing
        # in from a deep link (/settings/, /system-info/, etc.).
        # request.get_full_path() preserves any query string. The login
        # view validates `next` via url_has_allowed_host_and_scheme, so
        # an off-host value smuggled in here can't redirect outward.
        if request is not None:
            return redirect(
                f'{login_url}?{urlencode({"next": request.get_full_path()})}'
            )
        return redirect(login_url)

    def update_settings(
        self,
        request: 'HttpRequest',
        current_pass_correct: bool | None,
    ) -> None:
        new_user = request.POST.get('user', '')
        new_pass = request.POST.get('password', '')
        new_pass2 = request.POST.get('password2', '')
        # Handle auth components
        if self.settings['password']:  # if password currently set,
            if new_user != self.settings['user']:  # trying to change user
                # Should have current password set.
                # Optionally may change password.
                if current_pass_correct is None:
                    raise ValueError(
                        'Must supply current password to change username'
                    )
                if not current_pass_correct:
                    raise ValueError('Incorrect current password.')

                self.settings['user'] = new_user

            if new_pass:
                if current_pass_correct is None:
                    raise ValueError(
                        'Must supply current password to change password'
                    )
                if not current_pass_correct:
                    raise ValueError('Incorrect current password.')

                if new_pass2 != new_pass:  # changing password
                    raise ValueError('New passwords do not match!')

                self.settings['password'] = hash_password(new_pass)

        else:  # no current password
            if new_user:  # setting username and password
                if new_pass and new_pass != new_pass2:
                    raise ValueError('New passwords do not match!')
                if not new_pass:
                    raise ValueError('Must provide password')
                self.settings['user'] = new_user
                self.settings['password'] = hash_password(new_pass)
            else:
                raise ValueError('Must provide username')


def authorized(
    orig: Callable[P, R],
) -> 'Callable[P, R | HttpResponse]':
    # Note on the return type: when `R` is DRF's `Response` (which is itself
    # an `HttpResponse` subclass), mypy collapses `Response | HttpResponse`
    # to just `HttpResponse`, losing the `Response`-specific attributes
    # from the static type. This mirrors Django's own `@login_required`
    # decorator and is intentional — at runtime the wrapped view still
    # returns its concrete type. Callers that need the narrower type
    # should cast at the call site.
    from django.http import HttpRequest
    from rest_framework.request import Request

    from anthias_server.settings import settings

    @wraps(orig)
    def decorated(*args: P.args, **kwargs: P.kwargs) -> 'R | HttpResponse':
        if not settings.auth:
            return orig(*args, **kwargs)

        if len(args) == 0:
            raise ValueError('No request object passed to decorated function')

        request = args[-1]

        if not isinstance(request, (HttpRequest, Request)):
            raise ValueError(
                'Request object is not of type HttpRequest or Request'
            )

        auth_response = settings.auth.authenticate_if_needed(request)
        if auth_response is not None:
            return auth_response
        return orig(*args, **kwargs)

    return decorated
