"""Provider-neutral contract for inbound content import.

An ``ImportProvider`` knows how to talk to one third-party signage
platform's API. The three methods are the whole surface the rest of
Anthias depends on:

* ``validate_token``  — is this API token accepted?
* ``list_media``      — what's importable, and what has to be skipped?
* ``import_item``     — pull one item in and create the Anthias ``Asset``.

Errors follow the same split as ``lib.screenly_migration``:
``validate_token`` / ``list_media`` let ``requests.RequestException``
(or a provider's transport equivalent) propagate so the view can answer
502; ``import_item`` raises :class:`ProviderImportError` for per-item
failures the wizard shows in its error column, and lets transport
errors bubble.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderImportError(Exception):
    """A per-item import failure with an operator-facing message.

    Mirrors ``ScreenlyMigrationError``: the message is written for
    display in the wizard's per-item error column, so keep it short and
    concrete ("Yodeck download failed (404)"). ``user_message`` lets the
    view echo it into API responses without going through ``str(exc)`` —
    that keeps CodeQL's information-exposure rule quiet, since the
    attribute traces to a string we composed, not to exception/stack
    state.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message: str = message


@dataclass
class RemoteMediaItem:
    """One media item on the remote platform, before import.

    ``importable`` is False for media Anthias has no viewer path for
    (audio, documents); ``skip_reason`` then carries the operator-facing
    explanation. ``raw`` keeps the provider's original payload so a later
    ``import_item`` needn't re-fetch the list row.
    """

    remote_id: str
    name: str
    media_type: str
    importable: bool
    skip_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        # ``raw`` is intentionally omitted — it can carry provider
        # internals (and, for URL-ingested media, source URLs) that the
        # browser has no need to see.
        return {
            'remote_id': self.remote_id,
            'name': self.name,
            'media_type': self.media_type,
            'importable': self.importable,
            'skip_reason': self.skip_reason,
        }


@dataclass
class ImportOutcome:
    """Result of importing a single item.

    ``skipped`` distinguishes "deliberately not imported" (unsupported
    type, already imported) from a hard failure — the wizard and CLI
    tally the two separately. A skip is reported with ``success=False``
    unless it's an idempotent re-run of an item Anthias already holds,
    in which case ``success=True`` + ``asset_id`` point at the existing
    row.
    """

    success: bool
    asset_id: str | None = None
    skipped: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'success': self.success,
            'asset_id': self.asset_id,
            'skipped': self.skipped,
            'reason': self.reason,
        }


class ImportProvider(ABC):
    """Base class every import provider implements.

    The class attributes drive the settings page and wizard copy so the
    UI stays data-driven — a new provider appears in Settings by virtue
    of being registered, with no template edit.
    """

    #: URL-safe identifier used in routes (``/settings/import/<key>/``)
    #: and the registry. Lowercase, no spaces.
    key: str = ''
    #: Display name shown in the UI ("Yodeck").
    label: str = ''
    #: One-line description for the settings card.
    description: str = ''
    #: Guidance shown next to the token field in the wizard.
    token_help: str = ''

    @abstractmethod
    def validate_token(self, token: str) -> bool:
        """Return True if the token is accepted, False if rejected.

        Transport errors (network down, 5xx) propagate to the caller so
        "your token is bad" stays distinct from "the platform is down".
        """

    @abstractmethod
    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        """Enumerate the account's media as ``RemoteMediaItem`` rows."""

    @abstractmethod
    def import_item(
        self, token: str, remote_id: str, *, enable: bool = True
    ) -> ImportOutcome:
        """Import one item and create the Anthias ``Asset``.

        Raises :class:`ProviderImportError` for per-item failures;
        transport errors propagate.
        """
