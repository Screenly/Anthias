"""Mirror publish: make a screen's playlist match this controller's.

Uses only the screen's existing v2 API:

* file assets are uploaded with ``POST /file_asset`` (Content-Range
  chunks) and created with ``POST /assets``;
* settings that ``PATCH /assets/<id>`` accepts are patched in place;
* anything else (a new file, a changed URL or type) replaces the
  remote asset;
* remote assets that are not part of the mirror are deleted (mirror
  mode owns the whole playlist);
* the order is set last with ``POST /assets/order``.

The screen picks its own asset ids, so :class:`NodeAsset` remembers
which remote asset mirrors which local one. A publish is idempotent: a
second run with nothing changed only lists the screen's assets and
re-sends the order.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Protocol

from anthias_server.controller.client import NodeRequestError
from anthias_server.controller.models import Node, NodeAsset

# Dates for an asset that has none locally. Such an asset never plays
# (``Asset.is_active`` needs both dates), and the screen's create API
# requires both, so send a window that is already over.
_NEVER_START = datetime(1970, 1, 1, tzinfo=UTC)
_NEVER_END = datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)

# Fields ``PATCH /api/v2/assets/<id>`` accepts. A change to anything
# else (uri, mimetype, the file itself) means a new remote asset.
PATCHABLE = (
    'name',
    'start_date',
    'end_date',
    'duration',
    'is_enabled',
    'nocache',
    'skip_asset_check',
    'skip_ssl_verify',
    'play_days',
    'play_time_from',
    'play_time_to',
    'refresh_interval_s',
    'custom_headers',
)

# Compared against what the screen reports, to undo local edits made on
# the screen even when nothing changed on the controller.
_DRIFT_FIELDS = (
    'is_enabled',
    'nocache',
    'skip_asset_check',
    'skip_ssl_verify',
    'play_days',
)


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass
class ManifestItem:
    asset_id: str
    name: str
    spec: dict[str, Any]
    file_path: str | None = None
    content_sha256: str = ''

    @property
    def is_file(self) -> bool:
        return self.file_path is not None

    @property
    def identity_hash(self) -> str:
        """What can only change by replacing the remote asset.

        Compared with what was recorded at the last publish rather than
        with the screen's copy: the screen rewrites some of it (a
        remote video URL becomes a local file once downloaded).
        """
        return _hash(
            {
                'mimetype': self.spec.get('mimetype'),
                'uri': None if self.is_file else self.spec.get('uri'),
                'content': self.content_sha256,
            }
        )

    @property
    def spec_hash(self) -> str:
        return _hash(self.patch_body())

    def patch_body(self) -> dict[str, Any]:
        return {k: self.spec[k] for k in PATCHABLE if k in self.spec}


class AssetApi(Protocol):
    """The part of :class:`~.client.NodeClient` a publish uses."""

    def list_assets(self) -> list[dict[str, Any]]: ...

    def upload_file(self, file_path: str, filename: str) -> dict[str, Any]: ...

    def create_asset(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def update_asset(
        self, asset_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    def delete_asset(self, asset_id: str) -> None: ...

    def set_order(self, asset_ids: list[str]) -> None: ...


@dataclass
class Manifest:
    items: list[ManifestItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    uploaded_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f'{self.created} added',
            f'{self.updated} updated',
            f'{self.unchanged} unchanged',
            f'{self.deleted} removed',
        ]
        text = ', '.join(parts)
        if self.uploaded_bytes:
            text += f'; {self.uploaded_bytes / 1_000_000:.1f} MB uploaded'
        if self.errors:
            text += '. Errors: ' + '; '.join(self.errors)
        return text


@lru_cache(maxsize=256)
def _sha256_file(path: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns  # cache key only
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_sha256(path: str) -> str:
    stat = os.stat(path)
    return _sha256_file(path, stat.st_size, stat.st_mtime_ns)


def _iso(value: datetime | None, fallback: datetime) -> str:
    return (value or fallback).isoformat()


def _time(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _local_file(uri: str, assetdir: str) -> str | None:
    """The asset's file, if it is one stored on this device."""
    if not uri.startswith('/'):
        return None
    real = os.path.realpath(uri)
    base = os.path.realpath(assetdir)
    if os.path.commonpath([real, base]) != base:
        return None
    return real


def build_manifest() -> Manifest:
    """This controller's playlist, in play order."""
    from anthias_server.app.models import (
        Asset,
        clamp_duration,
        clamp_refresh_interval,
        normalize_asset_headers,
    )
    from anthias_server.settings import settings

    manifest = Manifest()
    assets = Asset.objects.order_by('play_order', 'name', 'asset_id')
    for asset in assets:
        name = asset.name or asset.asset_id
        uri = asset.uri or ''
        if asset.is_processing:
            manifest.skipped.append(f'{name} (still processing)')
            continue
        if not uri:
            manifest.skipped.append(f'{name} (no content)')
            continue
        metadata = asset.metadata or {}
        spec: dict[str, Any] = {
            'name': name,
            'mimetype': asset.mimetype or 'webpage',
            'start_date': _iso(asset.start_date, _NEVER_START),
            'end_date': _iso(asset.end_date, _NEVER_END),
            'duration': clamp_duration(asset.duration or 0),
            'is_enabled': bool(asset.is_enabled),
            'nocache': bool(asset.nocache),
            'skip_asset_check': bool(asset.skip_asset_check),
            'skip_ssl_verify': bool(asset.skip_ssl_verify),
            'play_days': asset.get_play_days(),
            'play_time_from': _time(asset.play_time_from),
            'play_time_to': _time(asset.play_time_to),
            'refresh_interval_s': clamp_refresh_interval(
                metadata.get('refresh_interval_s', 0)
            ),
            'custom_headers': normalize_asset_headers(metadata.get('headers')),
        }
        item = ManifestItem(asset_id=asset.asset_id, name=name, spec=spec)
        file_path = _local_file(uri, settings['assetdir'])
        if file_path is not None:
            if not os.path.isfile(file_path):
                manifest.skipped.append(f'{name} (file missing)')
                continue
            item.file_path = file_path
            item.content_sha256 = file_sha256(file_path)
        elif uri.startswith('/'):
            manifest.skipped.append(f'{name} (file outside the asset store)')
            continue
        else:
            spec['uri'] = uri
        manifest.items.append(item)
    return manifest


def _upload_name(item: ManifestItem) -> str:
    """A filename whose extension tells the screen the file type."""
    assert item.file_path is not None
    ext = os.path.splitext(item.file_path)[1]
    if not ext:
        ext = '.mp4' if item.spec.get('mimetype') == 'video' else '.png'
    return f'{item.asset_id}{ext.lower()}'


def _create_remote(
    client: AssetApi, item: ManifestItem, result: SyncResult
) -> str:
    body = dict(item.spec)
    if item.is_file:
        assert item.file_path is not None
        upload = client.upload_file(item.file_path, _upload_name(item))
        result.uploaded_bytes += os.path.getsize(item.file_path)
        body['uri'] = upload['uri']
        if upload.get('ext'):
            body['ext'] = upload['ext']
    created = client.create_asset(body)
    return str(created['asset_id'])


def _drifted(remote: dict[str, Any], item: ManifestItem) -> bool:
    for key in _DRIFT_FIELDS:
        if key in remote and remote[key] != item.spec.get(key):
            return True
    return (
        item.spec.get('mimetype') != 'video'
        and 'duration' in remote
        and remote['duration'] != item.spec.get('duration')
    )


def sync_node(node: Node, client: AssetApi, manifest: Manifest) -> SyncResult:
    """Push ``manifest`` to one screen. Network errors other than a
    per-asset refusal propagate, so the caller can mark the screen
    offline."""
    from django.utils import timezone

    result = SyncResult()
    remote = {str(a.get('asset_id')): a for a in client.list_assets()}
    mappings = {m.source_asset_id: m for m in node.assets.all()}
    ordered: list[str] = []
    keep: set[str] = set()

    for item in manifest.items:
        mapping = mappings.get(item.asset_id)
        spec_hash = item.spec_hash
        identity_hash = item.identity_hash
        try:
            remote_id = mapping.remote_asset_id if mapping else ''
            existing = remote.get(remote_id) if remote_id else None
            if (
                mapping is not None
                and existing is not None
                and mapping.identity_hash == identity_hash
            ):
                if mapping.spec_hash != spec_hash or _drifted(existing, item):
                    client.update_asset(remote_id, item.patch_body())
                    result.updated += 1
                else:
                    result.unchanged += 1
            else:
                new_id = _create_remote(client, item, result)
                if existing is not None:
                    client.delete_asset(remote_id)
                    remote.pop(remote_id, None)
                    result.deleted += 1
                remote_id = new_id
                result.created += 1
        except NodeRequestError as exc:
            result.errors.append(f'{item.name}: {exc}')
            if mapping and mapping.remote_asset_id in remote:
                keep.add(mapping.remote_asset_id)
                ordered.append(mapping.remote_asset_id)
            continue
        NodeAsset.objects.update_or_create(
            node=node,
            source_asset_id=item.asset_id,
            defaults={
                'remote_asset_id': remote_id,
                'identity_hash': identity_hash,
                'spec_hash': spec_hash,
                'synced_at': timezone.now(),
            },
        )
        keep.add(remote_id)
        ordered.append(remote_id)

    for remote_id in list(remote):
        if remote_id in keep:
            continue
        try:
            client.delete_asset(remote_id)
            result.deleted += 1
        except NodeRequestError as exc:
            result.errors.append(f'remove {remote_id}: {exc}')

    wanted = {item.asset_id for item in manifest.items}
    node.assets.exclude(source_asset_id__in=wanted).delete()

    client.set_order(ordered)
    return result
