"""Server-side proxy for migrating Anthias assets to Screenly v4.1.

The browser collects a Screenly API token but never sends files directly
to api.screenlyapp.com — for local image/video assets the bytes live on
the device's filesystem (``/data/anthias_assets/``) and aren't reachable
from the public internet, so the upload is fanned out one asset at a
time through this module. URL-backed assets (webpage, streaming) skip
the file read and go through with ``source_url``.

The token is never persisted; each request carries it inline and the
module forwards it straight to Screenly.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests

from anthias_common.http import AnthiasSession
from anthias_server.app.models import Asset
from anthias_server.app.views_files import ANTHIAS_ASSETS_ROOT


SCREENLY_API_BASE = 'https://api.screenlyapp.com/api/v4.1'

# Module-level session so every outbound migration call carries the
# Anthias User-Agent (see ``AnthiasSession``). The session is cheap to
# keep open and also gives us connection reuse across the typically
# 10-100 sequential per-asset calls a migration produces.
_session = AnthiasSession()

# Title used for the Screenly asset-group that migrated assets are
# placed under. Lives here so the prepare and per-asset paths agree
# on a single string — renaming the group is a one-line change.
MIGRATION_ASSET_GROUP_TITLE = 'Migrated from Anthias'

# Bounded so a slow Screenly response can't pin a request worker.
# Upload is the long pole; allow generously for that, terse for the
# token-validation probe.
_VALIDATE_TIMEOUT_S = 10.0
_UPLOAD_TIMEOUT_S = 120.0

# Multipart filename sanitisation. Drops path separators and control
# bytes so the operator-supplied ``Asset.name`` can't poison the S3
# key Screenly derives from the filename or trip up downstream
# tooling. We deliberately don't slugify further — Screenly's UI
# displays the multipart filename to the operator, and aggressive
# slugification ("My Day 2" → "my-day-2") would make migrated assets
# unrecognisable next to their Anthias originals.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f/\\]+')


class ScreenlyMigrationError(Exception):
    """Raised when a single-asset migration step fails for a known reason.

    The message is operator-facing — it surfaces in the UI's per-asset
    error column, so keep it short and concrete (e.g. "File not found
    on disk", "Screenly rejected upload: 413 payload too large").

    The ``user_message`` attribute mirrors the constructor argument so
    the view layer can include it in API responses without going
    through ``str(exc)``. That distinction is what keeps CodeQL's
    information-exposure rule quiet — the attribute traces to a
    string we composed for display, not to exception/stack state.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message: str = message


def _auth_headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Token {token}',
        # Without this Screenly returns 204 No Content on create — we
        # need the body to surface the asset id back to the UI.
        'Prefer': 'return=representation',
    }


def validate_token(token: str) -> bool:
    """Check whether a Screenly API token is accepted by v4.1.

    Probes ``GET /assets?limit=1`` because it's the cheapest authenticated
    endpoint that exercises the same auth path as the upload calls. A
    200 means the token is good; 401/403 means it isn't. Network errors
    propagate as ``requests.RequestException`` for the caller to translate
    into a user-visible message — we don't want to silently treat a
    transient outage as "bad token".
    """
    response = _session.get(
        f'{SCREENLY_API_BASE}/assets',
        headers=_auth_headers(token),
        params={'limit': 1},
        timeout=_VALIDATE_TIMEOUT_S,
    )
    if response.status_code == 200:
        return True
    if response.status_code in (401, 403):
        return False
    response.raise_for_status()
    return False


def _extract_group_id(response: requests.Response) -> str | None:
    """Pull the first asset-group ``id`` out of a Screenly response.

    Handles both shapes Screenly returns for asset-group endpoints:
    a list of rows (postgREST-style lookup) or a single dict (create
    response under ``Prefer: return=representation``). Returns ``None``
    if the body is unparseable, the wrong shape, or missing ``id``.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, list):
        if not body:
            return None
        body = body[0]
    if isinstance(body, dict):
        row_id = body.get('id')
        if isinstance(row_id, str):
            return row_id
    return None


def ensure_asset_group(token: str, title: str) -> str:
    """Return the ULID of the Screenly asset-group with this title.

    Idempotent get-or-create: queries postgREST-style
    ``?title=eq.<title>`` first, and only POSTs a new group if none
    exists. Migrations re-run repeatedly during testing and we don't
    want each rerun to litter the operator's account with duplicate
    "Migrated from Anthias (2)" groups.

    Raises ``ScreenlyMigrationError`` on Screenly-side rejection.
    Lets ``requests.RequestException`` bubble for transport errors.
    """
    # Typed as str/str so mypy can resolve requests.get(... params=...)
    # against its overloaded signature — the more permissive
    # dict[str, object] form here doesn't match any of the params
    # type unions.
    lookup_params: dict[str, str] = {'title': f'eq.{title}', 'limit': '1'}
    lookup = _session.get(
        f'{SCREENLY_API_BASE}/asset-groups',
        headers=_auth_headers(token),
        params=lookup_params,
        timeout=_VALIDATE_TIMEOUT_S,
    )
    if lookup.ok:
        group_id = _extract_group_id(lookup)
        if group_id is not None:
            return group_id

    create = _session.post(
        f'{SCREENLY_API_BASE}/asset-groups',
        headers={
            **_auth_headers(token),
            'Content-Type': 'application/json',
        },
        json={'title': title},
        timeout=_VALIDATE_TIMEOUT_S,
    )
    if not create.ok:
        raise ScreenlyMigrationError(
            f'Could not create Screenly asset group ({create.status_code}): '
            f'{_extract_screenly_error(create)}'
        )
    group_id = _extract_group_id(create)
    if group_id is not None:
        return group_id
    raise ScreenlyMigrationError(
        'Screenly asset-group create response was missing an id.'
    )


def _resolve_local_path(uri: str) -> Path | None:
    """Return a safe absolute path under ANTHIAS_ASSETS_ROOT, or None.

    Returns None for non-local URIs (http://, https://, youtube://, …).
    Uses the same realpath + startswith guard as ``views_files.anthias_assets``
    so a tampered DB row can't trick us into opening a file outside the
    asset directory.
    """
    if not uri.startswith(str(ANTHIAS_ASSETS_ROOT) + os.sep):
        return None
    base = os.path.realpath(ANTHIAS_ASSETS_ROOT) + os.sep
    target = os.path.realpath(uri)
    if not target.startswith(base):
        return None
    return Path(target)


def _build_upload_filename(asset: Asset, on_disk: Path) -> str:
    """Reconstruct an operator-recognisable filename for a local asset.

    Anthias renames every uploaded file to ``<uuid4>.<ext>`` on disk —
    the original raw filename is not stored anywhere. The closest
    reconstruction is the prettified ``Asset.name`` (e.g. "My Day 2")
    combined with the extension that survived on disk (".mp4"). That
    yields "My Day 2.mp4" — what the operator sees in the Anthias
    schedule, plus the format hint Screenly's UI needs.

    Falls back to the on-disk filename if ``Asset.name`` is blank,
    which is the worst case (a UUID) but never None.
    """
    base = (asset.name or '').strip()
    if not base:
        return on_disk.name
    sanitised = _UNSAFE_FILENAME_CHARS.sub('_', base).strip(' .')
    if not sanitised:
        return on_disk.name
    on_disk_ext = on_disk.suffix
    # If the operator-edited name already ends in the same extension,
    # don't double it up ("video.mp4.mp4" looks broken).
    if on_disk_ext and not sanitised.lower().endswith(on_disk_ext.lower()):
        sanitised = f'{sanitised}{on_disk_ext}'
    return sanitised


def _open_local_upload(
    asset: Asset, uri: str
) -> tuple[dict[str, Any] | None, Any]:
    """Open a local asset file for multipart upload.

    Returns ``(files_dict, file_handle)`` ready to pass into
    ``requests.post(..., files=...)``, or ``(None, None)`` if the URI
    isn't a local-asset path (the caller will fall back to ``source_url``).
    Caller owns closing the file handle.

    Raises ``ScreenlyMigrationError`` if the file is missing or
    unreadable on the device side — both surface as per-asset errors
    rather than 500s.
    """
    local_path = _resolve_local_path(uri)
    if local_path is None:
        return None, None
    if not local_path.is_file():
        raise ScreenlyMigrationError(
            f'File not found on device: {local_path.name}'
        )
    # is_file() races with a concurrent delete and doesn't catch
    # permission/IO problems on the read side; surface those as
    # per-asset errors instead of letting them bubble as 500s.
    try:
        file_handle = local_path.open('rb')
    except OSError as error:
        raise ScreenlyMigrationError(
            f'Could not read {local_path.name} on device: '
            f'{error.strerror or type(error).__name__}'
        ) from error
    upload_filename = _build_upload_filename(asset, local_path)
    return {'file': (upload_filename, file_handle)}, file_handle


def _parse_upload_response(
    response: requests.Response,
) -> dict[str, Any] | list[Any]:
    """Best-effort parse of Screenly's per-asset create response.

    Screenly's create endpoint returns a single-row array under
    ``Prefer: return=representation``, so list[Any] is the common
    shape; dict is tolerated for forward-compat. A 2xx with no body
    is uncommon but harmless — fall back to an empty dict so the
    caller can still report success.
    """
    try:
        parsed = response.json()
    except ValueError:
        return {}
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return parsed
    return {}


def migrate_asset(
    token: str,
    asset: Asset,
    asset_group_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Upload a single Anthias asset to Screenly.

    Image/video assets backed by a local file are POSTed as multipart;
    URL-backed assets (webpage, streaming) are POSTed with ``source_url``.
    The distinction is taken from the URI shape rather than the mimetype
    so the routing stays stable across Anthias' own internal mimetype
    rename history.

    ``asset_group_id`` (when supplied) places the new asset in the
    given Screenly asset-group — the wizard uses this to corral every
    migrated asset under a single "Migrated from Anthias" folder so
    the operator can find them after a cross-account move.

    Returns the parsed Screenly response body on success. Raises
    ``ScreenlyMigrationError`` with an operator-facing message on any
    failure that should be reported per-asset (file missing, Screenly
    rejection); lets ``requests`` exceptions bubble up so the API view
    can decide between 502 (network) and per-asset error reporting.
    """
    if not asset.uri:
        raise ScreenlyMigrationError('Asset has no URI to migrate.')

    title = (asset.name or '').strip() or asset.asset_id
    data: dict[str, str] = {'title': title}
    if asset_group_id:
        data['asset_group_id'] = asset_group_id

    files, file_handle = _open_local_upload(asset, asset.uri)
    if files is None:
        data['source_url'] = asset.uri

    try:
        response = _session.post(
            f'{SCREENLY_API_BASE}/assets',
            headers=_auth_headers(token),
            data=data,
            files=files,
            timeout=_UPLOAD_TIMEOUT_S,
        )
    finally:
        if file_handle is not None:
            file_handle.close()

    if not response.ok:
        detail = _extract_screenly_error(response)
        raise ScreenlyMigrationError(
            f'Screenly rejected upload ({response.status_code}): {detail}'
        )

    return _parse_upload_response(response)


def _extract_screenly_error(response: requests.Response) -> str:
    """Pull a one-line error string out of a non-OK Screenly response.

    Screenly's error bodies are JSON with either ``error`` or ``detail``,
    but a misbehaving proxy could return HTML — fall back to a clipped
    text snippet in that case so the UI still gets something useful.
    """
    try:
        body = response.json()
    except ValueError:
        snippet = response.text.strip().splitlines()[:1]
        return snippet[0][:200] if snippet else 'no response body'
    if isinstance(body, dict):
        for key in ('error', 'detail', 'message'):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return str(body)[:200]
