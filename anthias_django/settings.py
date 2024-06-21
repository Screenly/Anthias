"""
Django settings for anthias_django project.

Generated by 'django-admin startproject' using Django 3.2.18.

For more information on this file, see
https://docs.djangoproject.com/en/3.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.2/ref/settings/
"""

import logging
import pytz
import secrets
from pathlib import Path
from os import getenv

from lib.utils import get_node_ip
from retry.api import retry_call
from settings import settings as device_settings

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = getenv('ENVIRONMENT', 'production') == 'development'

if not DEBUG:
    if not device_settings.get('django_secret_key'):
        # Modify the generated so that string interpolation errors can be avoided.
        secret_key = secrets.token_urlsafe(50)
        device_settings['django_secret_key'] = secret_key
        device_settings.save()

    SECRET_KEY = device_settings.get('django_secret_key')
else:
    # SECURITY WARNING: keep the secret key used in production secret!
    SECRET_KEY = 'django-insecure-7rz*$)g6dk&=h-3imq2xw*iu!zuhfb&w6v482_vs!w@4_gha=j'

# @TODO: Resolve hostnames and IP addresses dynamically.
ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'anthias-nginx'
]

try:
    ALLOWED_HOSTS += retry_call(get_node_ip, tries=30, delay=1).split()
except Exception as error:
    logging.error('Failed to get the node IP address: %s', error)


# Application definition

INSTALLED_APPS = [
    'anthias_app.apps.AnthiasAppConfig',
    'drf_spectacular',
    'rest_framework',
    'api.apps.ApiConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'anthias_django.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'anthias_django.wsgi.application'


# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

USE_I18N = True

USE_L10N = True

USE_TZ = True

try:
    with open('/etc/timezone', 'r') as f:
        TIME_ZONE = f.read().strip()
        pytz.timezone(TIME_ZONE)  # Checks if the timezone is valid.
except (pytz.exceptions.UnknownTimeZoneError, FileNotFoundError):
    TIME_ZONE = 'UTC'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = '/data/screenly/staticfiles'

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'api.helpers.custom_exception_handler',
    # The project uses custom authentication classes, so we need to disable the default ones.
    'DEFAULT_AUTHENTICATION_CLASSES': []
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Anthias API',
    'VERSION': '1.2.0',
}