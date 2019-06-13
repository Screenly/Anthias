#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod
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

    @abstractmethod
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


class BasicAuth(Auth):
    def __init__(self, settings):
        self.settings = settings

    def _check(self, username, password):
        """
        Check username/password combo against database.
        :param username: str
        :param password: str
        :return: True if the check passes.
        """
        hashed_password = hashlib.sha256(password).hexdigest()
        return self.settings['user'] == username and self.settings['password'] == hashed_password

    @property
    def is_authorized(self):
        auth = request.authorization
        return auth and self._check(auth.username, auth.password)

    def authenticate(self):
        realm = "Screenly OSE {}".format(self.settings['player_name'])
        return Response("Access denied", 401, {"WWW-Authenticate": 'Basic realm="{}"'.format(realm)})


class WoTTAuth(BasicAuth):
    def __init__(self, settings):
        super(WoTTAuth, self).__init__(settings)
        # TODO: read credentials, store them into self.username and self.password

    def _check(self, username, password):
        # TODO: compare username and password with self.username and self.password
        return super(WoTTAuth, self)._check(username, password)


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
