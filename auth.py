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
        pass

    @abstractmethod
    def is_authorized(self):
        pass

    def auth(self):
        if not self.is_authorized:
            return self.authenticate()


class BasicAuth(Auth):
    def __init__(self, settings):
        self.settings = settings

    def _check(self, username, password):
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


def authorized(orig):
    from settings import settings

    @wraps(orig)
    def decorated(*args, **kwargs):
        if not settings.auth:
            return orig(*args, **kwargs)
        return settings.auth.auth() or orig(*args, **kwargs)
    return decorated
