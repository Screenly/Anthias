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


def _conf_get(config, section, field):  # type: ignore[no-untyped-def]
    """Trim helper around configparser — returns '' when the section
    or key doesn't exist."""
    if not config.has_section(section):
        return ''
    return config.get(section, field, fallback='').strip()


def _read_auth_state(path):  # type: ignore[no-untyped-def]
    """Return ``(config, auth_backend, username, password_hash)``
    extracted from anthias.conf, or ``None`` if the file can't be
    parsed."""
    config = configparser.ConfigParser()
    try:
        config.read(path)
    except configparser.Error:
        logging.exception('Could not parse %s; skipping auth migration', path)
        return None
    return (
        config,
        _conf_get(config, 'main', 'auth_backend'),
        _conf_get(config, 'auth_basic', 'user'),
        _conf_get(config, 'auth_basic', 'password'),
    )


def _promote_user(  # type: ignore[no-untyped-def]
    user_model,  # noqa: N803  (Django User model is conventionally `User`,
    username,  # but Sonar's S117 wants snake_case; rename the param
    password_hash,  # to keep the linter happy.)
):
    """Idempotent upsert of the operator User row from the conf hash."""
    user_model.objects.update_or_create(
        username=username,
        defaults={
            'password': password_hash,
            'is_staff': True,
            'is_superuser': True,
            'is_active': True,
        },
    )


def _migrate(apps, schema_editor):  # type: ignore[no-untyped-def]
    user_model = apps.get_model('auth', 'User')

    path = _conf_path()
    if path is None or not os.path.isfile(path):
        return

    state = _read_auth_state(path)
    if state is None:
        return
    config, auth_backend, username, password_hash = state

    # Three branches keyed on what the conf says about auth_basic:
    #   * complete + Django-format hash → promote to User row, keep
    #     ``auth_backend`` as 'auth_basic' so the feature flag still
    #     enforces login.
    #   * complete + legacy SHA256/plaintext hash → unverifiable, fail
    #     open: clear ``auth_backend`` so the device stays reachable.
    #   * ``auth_basic`` selected but creds missing/blank → previously
    #     would have gated everything against a no-User device →
    #     lockout. Same fail-open: clear ``auth_backend``.
    disable_auth = False
    if auth_backend == 'auth_basic':
        if not username or not password_hash:
            logging.error(
                'auth_basic enabled in %s but credentials are missing; '
                'disabling basic auth to avoid a lockout. Re-set the '
                'password from the Settings page.',
                path,
            )
            disable_auth = True
        elif _LEGACY_SHA256_HEX.match(password_hash) or '$' not in password_hash:
            logging.error(
                'Insecure password hash in %s; clearing credentials and '
                'disabling basic auth. Re-set the password from the '
                'Settings page.',
                path,
            )
            disable_auth = True
        else:
            _promote_user(user_model, username, password_hash)

    needs_write = False
    if disable_auth:
        config.set('main', 'auth_backend', '')
        needs_write = True

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
