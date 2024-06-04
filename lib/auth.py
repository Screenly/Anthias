#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from base64 import b64decode
from builtins import str
from builtins import object
from abc import ABCMeta, abstractmethod
from functools import wraps
import hashlib
import os.path
import json

from future.utils import with_metaclass


LINUX_USER = os.getenv('USER', 'pi')
WOTT_CREDENTIALS_PATH = '/opt/wott/credentials'
WOTT_USER_CREDENTIALS_PATH = os.path.join(WOTT_CREDENTIALS_PATH, LINUX_USER)
WOTT_ANTHIAS_CREDENTIAL_NAME = 'anthias'


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
        If the user performing the request is not authenticated, initiate authentication.
        :return: a Response which initiates authentication or None if already authenticated.
        """
        from django.http import HttpResponse

        try:
            if not self.is_authenticated(request):
                return self.authenticate()
        except ValueError as e:
            return HttpResponse("Authorization backend is unavailable: " + str(e), status=503)

    def update_settings(self, request, current_pass_correct):
        """
        Submit updated values from Settings page.
        :param current_pass_correct: the value of "Current Password" field or None if empty.
        :return:
        """
        pass

    @property
    def template(self):
        """
        Get HTML template and its context object to be displayed in Settings page.
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
    config = {
        'auth_basic': {
            'user': '',
            'password': ''
        }
    }

    def __init__(self, settings):
        self.settings = settings

    def _check(self, username, password):
        """
        Check username/password combo against database.
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        return self.settings['user'] == username and self.check_password(password)

    def check_password(self, password):
        hashed_password = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return self.settings['password'] == hashed_password

    def is_authenticated(self, request):
        authorization = request.headers.get('Authorization')
        if not authorization:
            return False

        content = authorization.split(' ')

        if len(content) != 2:
            return False

        auth_type = content[0]
        auth_data = content[1]
        if auth_type == 'Basic':
            auth_data = b64decode(auth_data).decode('utf-8')
            auth_data = auth_data.split(':')
            if len(auth_data) == 2:
                username = auth_data[0]
                password = auth_data[1]
                return self._check(username, password)
        return False

    @property
    def template(self):
        return 'auth_basic.html', {'user': self.settings['user']}

    def authenticate(self):
        from django.http import HttpResponse
        realm = "Anthias {}".format(self.settings['player_name'])
        return HttpResponse("Access denied", status=401, headers={"WWW-Authenticate": 'Basic realm="{}"'.format(realm)})

    def update_settings(self, request, current_pass_correct):
        new_user = request.POST.get('user', '')
        new_pass = request.POST.get('password', '').encode('utf-8')
        new_pass2 = request.POST.get('password2', '').encode('utf-8')
        new_pass = hashlib.sha256(new_pass).hexdigest() if new_pass else None
        new_pass2 = hashlib.sha256(new_pass2).hexdigest() if new_pass else None
        # Handle auth components
        if self.settings['password']:  # if password currently set,
            if new_user != self.settings['user']:  # trying to change user
                # should have current password set. Optionally may change password.
                if current_pass_correct is None:
                    raise ValueError("Must supply current password to change username")
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                self.settings['user'] = new_user

            if new_pass:
                if current_pass_correct is None:
                    raise ValueError("Must supply current password to change password")
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                if new_pass2 != new_pass:  # changing password
                    raise ValueError("New passwords do not match!")

                self.settings['password'] = new_pass

        else:  # no current password
            if new_user:  # setting username and password
                if new_pass and new_pass != new_pass2:
                    raise ValueError("New passwords do not match!")
                if not new_pass:
                    raise ValueError("Must provide password")
                self.settings['user'] = new_user
                self.settings['password'] = new_pass
            else:
                raise ValueError("Must provide username")


class WoTTAuth(BasicAuth):
    display_name = 'WoTT'
    name = 'auth_wott'
    config = {
        'auth_wott': {
            'wott_secret_name': 'anthias_credentials',
        }
    }

    def __init__(self, settings):
        super(WoTTAuth, self).__init__(settings)

    def update_settings(self, request, current_pass_correct):
        if not self._fetch_credentials():
            raise ValueError("Can not read WoTT credentials file or login credentials record is incorrect")

    def _fetch_credentials(self):
        wott_credentials_path = os.path.join(WOTT_USER_CREDENTIALS_PATH, WOTT_ANTHIAS_CREDENTIAL_NAME + ".json")

        if 'wott_secret_name' in self.settings and self.settings['wott_secret_name']:
            credentials_path = os.path.join(WOTT_CREDENTIALS_PATH, self.settings['wott_secret_name'] + ".json")
            if os.path.isfile(credentials_path):
                wott_credentials_path = credentials_path

        self.user = self.password = ''

        if not os.path.isfile(wott_credentials_path):
            return False

        with open(wott_credentials_path, "r") as credentials_file:
            credentials = json.load(credentials_file)
            login_record = credentials.get('login', '')
            if not login_record:
                return False
            login_record = login_record.split(':', 1)
            if len(login_record) == 2:
                self.user, password = login_record
                if password:
                    self.password = hashlib.sha256(password).hexdigest()
                else:
                    self.password = password

        return True

    def check_password(self, password):
        hashed_password = hashlib.sha256(password).hexdigest()
        return self.password == hashed_password

    @property
    def is_authenticated(self):
        if not self._fetch_credentials():
            raise ValueError('Cannot load credentials')
        return super(WoTTAuth, self).is_authenticated

    def _check(self, username, password):
        """
        Check username/password combo against WoTT Credentials.
        Used credentials with name 'anthias_credentials' or name
        which defined in value of 'anthias_credentials' settings
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        return self.user == username and self.check_password(password)

    @property
    def template(self):
        return None


def authorized(orig):
    from settings import settings
    from django.http import HttpRequest
    from rest_framework.request import Request

    @wraps(orig)
    def decorated(*args, **kwargs):
        if not settings.auth:
            return orig(*args, **kwargs)

        if len(args) == 0:
            raise ValueError('No request object passed to decorated function')

        request = args[-1]

        if not isinstance(request, (HttpRequest, Request)):
            raise ValueError('Request object is not of type HttpRequest or Request')

        return settings.auth.authenticate_if_needed(request) or orig(*args, **kwargs)

    return decorated
