"""Migrate `[auth_basic]` credentials from anthias.conf into a Django
User row, then strip the user/password fields from the conf file.

Pre-rebrand installs stored the operator's credentials in
``$HOME/.anthias/anthias.conf`` (or, in older deployments, the legacy
``$HOME/.screenly/screenly.conf``). The hash there is already
PBKDF2-format because ``BasicAuth.update_settings`` had been routing
through ``django.contrib.auth.hashers.make_password`` for a while, so
the stored byte-string drops straight into ``User.password`` without
re-hashing — operators do not have to reset their password during the
upgrade.

Idempotent. Re-runs no-op once the credentials have been moved out of
the conf and a matching User row exists.

Devices that were running with auth disabled (``auth_backend == ''``)
or that have a legacy bare-SHA256 hash in the conf produce no User
row; the latter is logged so the operator notices on next boot.
"""

from __future__ import annotations

import configparser
import logging
import os
import re

from django.db import migrations

_LEGACY_SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')


def _conf_path() -> str | None:
    home = os.environ.get('HOME')
    if not home:
        return None
    return os.path.join(home, '.anthias', 'anthias.conf')


def _migrate(apps, schema_editor):  # type: ignore[no-untyped-def]
    User = apps.get_model('auth', 'User')

    path = _conf_path()
    if path is None or not os.path.isfile(path):
        return

    config = configparser.ConfigParser()
    try:
        config.read(path)
    except configparser.Error:
        logging.exception('Could not parse %s; skipping auth migration', path)
        return

    auth_backend = (
        config.get('main', 'auth_backend', fallback='').strip()
        if config.has_section('main')
        else ''
    )

    username = (
        config.get('auth_basic', 'user', fallback='').strip()
        if config.has_section('auth_basic')
        else ''
    )
    password_hash = (
        config.get('auth_basic', 'password', fallback='').strip()
        if config.has_section('auth_basic')
        else ''
    )

    needs_write = False

    if auth_backend == 'auth_basic' and username and password_hash:
        if _LEGACY_SHA256_HEX.match(password_hash) or '$' not in password_hash:
            # Legacy SHA256 (pre-Django) or plaintext / unrecognised
            # format. We can't verify it against Django's hashers, so
            # the operator must re-set credentials. Disable auth so the
            # device stays reachable.
            logging.error(
                'Insecure password hash in %s; clearing credentials and '
                'disabling basic auth. Re-set the password from the '
                'Settings page.',
                path,
            )
            config.set('main', 'auth_backend', '')
            needs_write = True
        else:
            User.objects.update_or_create(
                username=username,
                defaults={
                    'password': password_hash,
                    'is_staff': True,
                    'is_superuser': True,
                    'is_active': True,
                },
            )

    # Strip the migrated credentials from the conf — DB is authoritative
    # from this point on. Wipe the whole [auth_basic] section so a
    # future ``settings.save()`` (which only writes keys it knows
    # about) doesn't resurrect a stale half-record.
    if config.has_section('auth_basic'):
        config.remove_section('auth_basic')
        needs_write = True

    if needs_write:
        try:
            with open(path, 'w') as f:
                config.write(f)
        except OSError:
            logging.exception('Failed to rewrite %s after auth migration', path)


class Migration(migrations.Migration):
    dependencies = [
        ('anthias_app', '0004_asset_schedule_fields'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(_migrate, reverse_code=migrations.RunPython.noop),
    ]
