#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod, abstractproperty
from functools import wraps
import hashlib
import os.path
import json

from flask import request, Response

LINUX_USER = os.getenv('USER', 'pi')
WOTT_CREDENTIALS_PATH = '/opt/wott/credentials'
WOTT_USER_CREDENTIALS_PATH = os.path.join(WOTT_CREDENTIALS_PATH, LINUX_USER)
WOTT_SCREENLY_CREDENTIAL_NAME = 'screenly'


class Auth(object):
    __metaclass__ = ABCMeta

    @abstractmethod
    def authenticate(self):
        """
        Let the user authenticate himself.
        :return: a Response which initiates authentication.
        """
        pass

    @abstractproperty
    def is_authenticated(self):
        """
        See if the user is authenticated for the request.
        :return: bool
        """
        pass

    def authenticate_if_needed(self):
        """
        If the user performing the request is not authenticated, initiate authentication.
        :return: a Response which initiates authentication or None if already authenticated.
        """
        try:
            if not self.is_authenticated:
                return self.authenticate()
        except ValueError as e:
            return Response("Authorization backend is unavailable: " + str(e), 503)

    def update_settings(self, current_pass_correct):
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

    def is_authenticated(self):
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
        hashed_password = hashlib.sha256(password).hexdigest()
        return self.settings['password'] == hashed_password

    @property
    def is_authenticated(self):
        auth = request.authorization
        return auth and self._check(auth.username, auth.password)

    @property
    def template(self):
        return 'auth_basic.html', {'user': self.settings['user']}

    def authenticate(self):
        realm = "Screenly OSE {}".format(self.settings['player_name'])
        return Response("Access denied", 401, {"WWW-Authenticate": 'Basic realm="{}"'.format(realm)})

    def update_settings(self, current_pass_correct):
        new_user = request.form.get('user', '')
        new_pass = request.form.get('password', '')
        new_pass2 = request.form.get('password2', '')
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
            'wott_secret_name': 'screenly_credentials',
        }
    }

    def __init__(self, settings):
        super(WoTTAuth, self).__init__(settings)

    def update_settings(self, current_pass_correct):
        if not self._fetch_credentials():
            raise ValueError("Can not read WoTT credentials file or login credentials record is incorrect")

    def _fetch_credentials(self):
        wott_credentials_path = os.path.join(WOTT_USER_CREDENTIALS_PATH, WOTT_SCREENLY_CREDENTIAL_NAME + ".json")

        if 'wott_secret_name' in self.settings and self.settings['wott_secret_name']:
            screenly_credentials_path = os.path.join(WOTT_CREDENTIALS_PATH, self.settings['wott_secret_name'] + ".json")
            if os.path.isfile(screenly_credentials_path):
                wott_credentials_path = screenly_credentials_path

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
        Used credentials with name 'screenly_credentials' or name
        which defined in value of 'screenly_credentials' settings
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        return self.user == username and self.check_password(password)

    @property
    def template(self):
        return None


def authorized(orig):
    """
    Annotation which initiates authentication if the request is unauthorized.
    :param orig: Flask function
    :return: Response
    """
    from settings import settings

    @wraps(orig)
    def decorated(*args, **kwargs):
        if not settings.auth:
            return orig(*args, **kwargs)
        return settings.auth.authenticate_if_needed() or orig(*args, **kwargs)

    return decorated
