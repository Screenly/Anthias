"""Keep /ws authorization tied to the credentials it was granted under.

``AssetConsumer`` resolves ``scope['user']`` once, at handshake time,
and a rotation doesn't change the resulting object —
``is_authenticated`` is True for any real User row whatever its
password is now. :func:`~anthias_server.app.consumers.disconnect_all`
is what invalidates those sockets, so the question is only ever *who
calls it*.

The settings-save paths call it explicitly for an ``auth_backend``
toggle, which never touches a User row. Credential changes can't be
handled that way, because the settings page is not the only thing that
makes them: ``/admin`` is a routed URL and the stock ``UserAdmin``
ships a change-password form. Hooking the model's own save is the one
place that sees all of them at once — and a call site added later gets
the revocation without having to remember it.

Scope, precisely: what this buys is a *fail-closed* revocation for
credential changes made **inside the uvicorn process** — the settings
page, the v2 API, and ``/admin``. Those share the in-process auth
generation with every open socket, so they are revoked whether or not
the channel layer is up. A change made from another process —
``manage.py changepassword``, a shell on the device, a Celery worker —
runs this receiver in *that* process, where bumping its own generation
counter reaches nobody; all such a change has is the Redis fan-out,
which is best-effort. Making those fail closed too would mean the
serving process re-reading credential state from the DB on a timer or
per frame, which is the SQLite/SBC cost the generation exists to
avoid. See :data:`anthias_server.app.consumers._auth_generation`.
"""

import logging
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)

_DISPATCH_UID = 'anthias_revoke_ws_authorization'

# Django writes ``last_login`` through ``save(update_fields=[...])`` on
# every successful login. That is the one User write that must not
# bounce every open socket: it happens because a session was just
# created, not because one was invalidated, and revoking on it would
# drop the operator's dashboard socket at the exact moment they log in.
#
# Everything else revokes, including a save whose ``update_fields`` we
# don't recognise. That is deliberate — the default is fail-closed, and
# it means the deactivation flags (``is_active``, ``is_staff``,
# ``is_superuser``) are covered too, since those decide who may hold a
# session just as much as the password does.
_SESSION_NEUTRAL_FIELDS = frozenset({'last_login'})


def revoke_ws_authorization(
    sender: type[User], instance: User, **kwargs: Any
) -> None:
    """Drop every open /ws socket when an operator account changes.

    Deleting the row counts as well as writing it: the socket's
    already-resolved ``scope['user']`` outlives the User it was
    resolved from, so a deleted account would otherwise keep streaming.

    Deferred to ``transaction.on_commit`` because this signal fires
    *inside* the caller's transaction, and Django's admin wraps its
    change form in one — so the write we're reacting to may still roll
    back. The generation bump can't roll back with it, and a socket
    that also missed the close would then be silent for good over a
    credential change that never happened. Outside a transaction
    (``manage.py``, a shell, and the settings views, since this project
    doesn't set ATOMIC_REQUESTS) ``on_commit`` runs the callback
    immediately, so nothing is delayed on the paths that matter.
    """
    update_fields = kwargs.get('update_fields')
    if (
        update_fields is not None
        and set(update_fields) <= _SESSION_NEUTRAL_FIELDS
    ):
        return

    # Imported lazily: consumers.py pulls in Channels, which the viewer
    # image deliberately doesn't ship (see INSTALLED_APPS in
    # django_project/settings.py) even though this app — and therefore
    # this receiver — is installed there too.
    from anthias_server.app.consumers import disconnect_all

    logger.debug(
        'Revoking /ws authorization after a change to user %r', instance.pk
    )
    transaction.on_commit(disconnect_all)


def register() -> None:
    """Connect the receiver. Called from ``AnthiasAppConfig.ready()``.

    An explicit call rather than ``@receiver`` plus a side-effect
    import in ``apps.py``: that import is unused by definition and
    would need a ``# noqa: F401`` to pass lint, which CLAUDE.md rules
    out when an idiom fixes the root cause. ``dispatch_uid`` keeps a
    second call idempotent.
    """
    post_save.connect(
        revoke_ws_authorization, sender=User, dispatch_uid=_DISPATCH_UID
    )
    post_delete.connect(
        revoke_ws_authorization, sender=User, dispatch_uid=_DISPATCH_UID
    )
