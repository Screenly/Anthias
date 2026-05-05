"""Migrate `[auth_basic]` credentials from anthias.conf into a Django
User row, then strip the user/password fields from the conf file.

Looks for ``$HOME/.anthias/anthias.conf`` only. Pre-rebrand
installations had their conf at ``$HOME/.screenly/screenly.conf``,
but ``bin/migrate_legacy_paths.sh`` runs *before* Django comes up
and renames the config dir + file to the new names (leaving a
back-compat symlink), so by the time this migration executes the
conf is always at the new path.

The hash in the conf is already PBKDF2-format because
``BasicAuth.update_settings`` had been routing through
``django.contrib.auth.hashers.make_password`` for a while, so the
stored byte-string drops straight into ``User.password`` without
re-hashing — operators do not have to reset their password during
the upgrade.

Idempotent. Re-runs no-op once the credentials have been moved out
of the conf and a matching User row exists.

Devices that were running with auth disabled (``auth_backend == ''``)
or that have a legacy bare-SHA256 hash in the conf produce no User
row; the latter is logged so the operator notices on next boot.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import tempfile

from django.db import migrations, transaction

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


def _atomic_write_conf(config, path):  # type: ignore[no-untyped-def]
    """Atomically rewrite anthias.conf.

    Writes to a tempfile in the same directory (so ``os.replace`` is a
    cheap intra-filesystem rename) and then swaps it into place. A
    crash mid-write never leaves a half-written conf; the original
    file stays intact until the rename succeeds.

    Raising on failure is intentional — the caller is inside a Django
    atomic block, and bubbling the exception lets the DB-side User
    upsert roll back so the migration retries cleanly on next boot
    instead of leaving the device with a User row but a stale conf.
    """
    parent = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(
        dir=parent, prefix='.anthias.conf.', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w') as f:
            config.write(f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@transaction.atomic
def _migrate(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Atomic across both stores: the DB writes (User upsert,
    auth_backend feature-flag flip) and the conf rewrite either all
    happen or none do.

    The DB side is wrapped by ``@transaction.atomic`` (RunPython
    already runs inside one when the backend supports DDL transactions
    — SQLite does — but the explicit decorator makes the contract
    legible). The conf-file side is made atomic by
    ``_atomic_write_conf`` (tempfile + os.replace). Critically, the
    conf write happens AFTER all DB operations so any IO error there
    bubbles up and rolls back the User upsert; the migration then
    retries cleanly on next boot rather than leaving the device with
    a half-applied state.
    """
    user_model = apps.get_model('auth', 'User')

    path = _conf_path()
    if path is None or not os.path.isfile(path):
        return

    state = _read_auth_state(path)
    if state is None:
        return
    config, auth_backend, username, password_hash = state

    # Classify the conf's stored credentials once; both the promotion
    # decision and the auth-backend feature-flag fix-up below key off
    # the same predicates.
    creds_present = bool(username and password_hash)
    creds_django_format = creds_present and (
        not _LEGACY_SHA256_HEX.match(password_hash) and '$' in password_hash
    )

    # Promote any Django-format hash into a User row regardless of
    # whether auth is currently enabled. Operators who configured
    # Basic auth and later toggled it off via the Settings page still
    # have their hash sitting in the conf; preserving it here keeps
    # re-enable from forcing a fresh password.
    if creds_django_format:
        _promote_user(user_model, username, password_hash)

    # Fix up the auth_backend feature flag when it points at a
    # configuration we can't honour:
    #   * ``auth_basic`` selected but creds missing/blank → would gate
    #     everything against a no-User device (lockout). Fail open by
    #     clearing the flag.
    #   * ``auth_basic`` selected with a legacy SHA256 / plaintext hash
    #     → unverifiable by Django's hashers. Same fail-open.
    disable_auth = False
    if auth_backend == 'auth_basic':
        if not creds_present:
            logging.error(
                'auth_basic enabled in %s but credentials are missing; '
                'disabling basic auth to avoid a lockout. Re-set the '
                'password from the Settings page.',
                path,
            )
            disable_auth = True
        elif not creds_django_format:
            logging.error(
                'Insecure password hash in %s; clearing credentials and '
                'disabling basic auth. Re-set the password from the '
                'Settings page.',
                path,
            )
            disable_auth = True

    needs_write = False
    if disable_auth:
        config.set('main', 'auth_backend', '')
        needs_write = True

    # Strip the migrated credentials from the conf — DB is authoritative
    # from this point on. Safe to remove unconditionally because we
    # already promoted Django-format hashes above; legacy/empty hashes
    # are unverifiable anyway and the operator must re-set them via
    # the UI.
    if config.has_section('auth_basic'):
        config.remove_section('auth_basic')
        needs_write = True

    if needs_write:
        # Re-raises on failure so the surrounding ``@transaction.atomic``
        # rolls back the DB side. Don't swallow the OSError here — a
        # half-applied state (User row created but conf still claiming
        # auth_basic with stale creds) would silently break re-enable.
        _atomic_write_conf(config, path)


class Migration(migrations.Migration):
    dependencies = [
        ('anthias_app', '0004_asset_schedule_fields'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(_migrate, reverse_code=migrations.RunPython.noop),
    ]
