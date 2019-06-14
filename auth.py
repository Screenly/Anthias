#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod, abstractproperty
from functools import wraps
import hashlib

from flask import request, Response


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
    def is_authorized(self):
        """
        See if the user is authorized for the request.
        :return: bool
        """
        pass

    def authorize(self):
        """
        If the request is not authorized, let the user authenticate himself.
        :return: a Response which initiates authentication or None if authorized.
        """
        if not self.is_authorized:
            return self.authenticate()

    def update_settings(self, current_password):
        pass

    @property
    def template(self):
        pass


class NoAuth(Auth):
    name = 'Disabled'
    id = ''
    config = {}

    def is_authorized(self):
        return True

    def authenticate(self):
        pass


class BasicAuth(Auth):
    name = 'Basic'
    id = 'auth_basic'
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
    def is_authorized(self):
        auth = request.authorization
        return auth and self._check(auth.username, auth.password)

    @property
    def template(self):
        return 'auth_basic.html', {'user': self.settings['user']}

    def authenticate(self):
        realm = "Screenly OSE {}".format(self.settings['player_name'])
        return Response("Access denied", 401, {"WWW-Authenticate": 'Basic realm="{}"'.format(realm)})

    def update_settings(self, current_pass):
        current_pass_correct = self.check_password(current_pass)
        new_user = request.form.get('user', '')
        new_pass = request.form.get('password', '')
        new_pass2 = request.form.get('password2', '')
        new_pass = '' if new_pass == '' else hashlib.sha256(new_pass).hexdigest()
        new_pass2 = '' if new_pass2 == '' else hashlib.sha256(new_pass2).hexdigest()
        # Handle auth components
        if self.settings['password'] != '':  # if password currently set,
            if new_user != self.settings['user']:  # trying to change user
                # should have current password set. Optionally may change password.
                if not current_pass:
                    raise ValueError("Must supply current password to change username")
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                self.settings['user'] = new_user

            if new_pass != '':
                if not current_pass:
                    raise ValueError("Must supply current password to change password")
                if not current_pass_correct:
                    raise ValueError("Incorrect current password.")

                if new_pass2 != new_pass:  # changing password
                    raise ValueError("New passwords do not match!")

                self.settings['password'] = new_pass

        else:  # no current password
            if new_user != '':  # setting username and password
                if new_pass != '' and new_pass != new_pass2:
                    raise ValueError("New passwords do not match!")
                if new_pass == '':
                    raise ValueError("Must provide password")
                self.settings['user'] = new_user
                self.settings['password'] = new_pass
            else:
                raise ValueError("Must provide username")


class WoTTAuth(BasicAuth):
    name = 'WoTT'
    id = 'auth_wott'
    config = {
        'auth_wott': {
            # TODO: return real settings
        }
    }

    def __init__(self, settings):
        super(WoTTAuth, self).__init__(settings)
        # TODO: read credentials, store them into self.username and self.password

    def _check(self, username, password):
        # TODO: compare username and password with self.username and self.password
        return super(WoTTAuth, self)._check(username, password)

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
        return settings.auth.authorize() or orig(*args, **kwargs)
    return decorated
