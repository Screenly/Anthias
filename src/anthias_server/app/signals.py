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
ships a change-password form, ``manage.py changepassword`` exists, and
so does a shell on the device. Hooking the model's own save is the one
place that sees all of them at once — and a call site added later gets
the revocation without having to remember it.
"""

import logging
from typing import Any

from django.contrib.auth.models import User
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

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


@receiver(
    [post_save, post_delete],
    sender=User,
    dispatch_uid='anthias_revoke_ws_authorization',
)
def revoke_ws_authorization(
    sender: type[User], instance: User, **kwargs: Any
) -> None:
    """Drop every open /ws socket when an operator account changes.

    Deleting the row counts as well as writing it: the socket's
    already-resolved ``scope['user']`` outlives the User it was
    resolved from, so a deleted account would otherwise keep streaming.
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
    disconnect_all()
