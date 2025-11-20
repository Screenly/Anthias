#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import hashlib
import os.path
from abc import ABCMeta, abstractmethod
from base64 import b64decode
from builtins import object, str
from functools import wraps

from future.utils import with_metaclass

LINUX_USER = os.getenv('USER', 'pi')


class Auth(with_metaclass(ABCMeta, object)):
    @abstractmethod
    def authenticate(self):
        """
        Let the user authenticate himself.
        :return: a Response which initiates authentication.
        """
        pass

    def is_authenticated(self, request):
        """
        See if the user is authenticated for the request.
        :return: bool
        """
        pass

    def authenticate_if_needed(self, request):
        """
        If the user performing the request is not authenticated, initiate
        authentication.

        :return: a Response which initiates authentication or None
        if already authenticated.
        """
        from django.http import HttpResponse

        try:
            if not self.is_authenticated(request):
                return self.authenticate()
        except ValueError as e:
            return HttpResponse(
                'Authorization backend is unavailable: ' + str(e), status=503
            )

    def update_settings(self, request, current_pass_correct):
        """
        Submit updated values from Settings page.
        :param current_pass_correct: the value of "Current Password" field
        or None if empty.

        :return:
        """
        pass

    @property
    def template(self):
        """
        Get HTML template and its context object to be displayed in
        the vettings page.

        :return: (template, context)
        """
        pass

    def check_password(self, password):
        """
        Checks if password correct.
        :param password: str
        :return: bool
        """
        pass


class NoAuth(Auth):
    display_name = 'Disabled'
    name = ''
    config = {}

    def is_authenticated(self, request):
        return True

    def authenticate(self):
        pass

    def check_password(self, password):
        return True


class BasicAuth(Auth):
    display_name = 'Basic'
    name = 'auth_basic'
    config = {'auth_basic': {'user': '', 'password': ''}}

    def __init__(self, settings):
        self.settings = settings

    def _check(self, username, password):
        """
        Check username/password combo against database.
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        return self.settings['user'] == username and self.check_password(
            password
        )

    def check_password(self, password):
        hashed_password = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return self.settings['password'] == hashed_password

    def is_authenticated(self, request):
        # First check Authorization header for API requests
        authorization = request.headers.get('Authorization')
        if authorization:
            content = authorization.split(' ')
            if len(content) == 2:
                auth_type = content[0]
                auth_data = content[1]
                if auth_type == 'Basic':
                    auth_data = b64decode(auth_data).decode('utf-8')
                    auth_data = auth_data.split(':')
                    if len(auth_data) == 2:
                        username = auth_data[0]
                        password = auth_data[1]
                        return self._check(username, password)

        # Then check session for form-based login
        username = request.session.get('auth_username')
        password = request.session.get('auth_password')
        if username and password:
            return self._check(username, password)

        return False

    @property
    def template(self):
        return 'auth_basic.html', {'user': self.settings['user']}

    def authenticate(self):
        from django.shortcuts import redirect
        from django.urls import reverse

        return redirect(reverse('anthias_app:login'))

    def update_settings(self, request, current_pass_correct):
        new_user = request.POST.get('user', '')
        new_pass = request.POST.get('password', '').encode('utf-8')
        new_pass2 = request.POST.get('password2', '').encode('utf-8')
        new_pass = hashlib.sha256(new_pass).hexdigest() if new_pass else None
        new_pass2 = hashlib.sha256(new_pass2).hexdigest() if new_pass else None
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

                self.settings['password'] = new_pass

        else:  # no current password
            if new_user:  # setting username and password
                if new_pass and new_pass != new_pass2:
                    raise ValueError('New passwords do not match!')
                if not new_pass:
                    raise ValueError('Must provide password')
                self.settings['user'] = new_user
                self.settings['password'] = new_pass
            else:
                raise ValueError('Must provide username')


def authorized(orig):
    from django.http import HttpRequest
    from rest_framework.request import Request

    from settings import settings

    @wraps(orig)
    def decorated(*args, **kwargs):
        if not settings.auth:
            return orig(*args, **kwargs)

        if len(args) == 0:
            raise ValueError('No request object passed to decorated function')

        request = args[-1]

        if not isinstance(request, (HttpRequest, Request)):
            raise ValueError(
                'Request object is not of type HttpRequest or Request'
            )

        return settings.auth.authenticate_if_needed(request) or orig(
            *args, **kwargs
        )

    return decorated
