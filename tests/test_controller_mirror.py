"""Mirror publish: diffing this controller's playlist onto a screen."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Self

import pytest
from django.utils import timezone

from anthias_server.app.models import Asset
from anthias_server.controller import mirror, service, vault
from anthias_server.controller.client import (
    NodeOfflineError,
    NodeRequestError,
)
from anthias_server.controller.models import (
    AuditEntry,
    Node,
    NodeAsset,
    NodeStatus,
    PublishState,
)


class FakeScreen:
    """In-memory stand-in for a screen's v2 asset API."""

    def __init__(self) -> None:
        self.assets: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.uploads = 0
        self.fail_create_for: set[str] = set()
        self._next = 0

    def _id(self) -> str:
        self._next += 1
        return f'remote{self._next}'

    def list_assets(self) -> list[dict[str, Any]]:
        self.calls.append(('list', ''))
        return [dict(a) for a in self.assets.values()]

    def upload_file(self, file_path: str, filename: str) -> dict[str, Any]:
        self.uploads += 1
        self.calls.append(('upload', filename))
        return {'uri': f'/data/{filename}.tmp', 'ext': '.png'}

    def create_asset(self, body: dict[str, Any]) -> dict[str, Any]:
        if body['name'] in self.fail_create_for:
            raise NodeRequestError('POST /assets: HTTP 400: nope', 400)
        asset_id = self._id()
        self.calls.append(('create', body['name']))
        self.assets[asset_id] = {'asset_id': asset_id, **body}
        return self.assets[asset_id]

    def update_asset(
        self, asset_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(('update', asset_id))
        self.assets[asset_id].update(body)
        return self.assets[asset_id]

    def delete_asset(self, asset_id: str) -> None:
        self.calls.append(('delete', asset_id))
        self.assets.pop(asset_id, None)

    def set_order(self, asset_ids: list[str]) -> None:
        self.calls.append(('order', ','.join(asset_ids)))
        self.order = list(asset_ids)

    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] not in ('list', 'order')]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _vault_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setattr(vault, 'key_path', lambda: str(tmp_path / 'k.key'))
    vault.reset_cache()
    yield
    vault.reset_cache()


@pytest.fixture
def node() -> Node:
    return Node.objects.create(
        name='Lobby',
        host='10.0.0.5',
        fingerprint='a' * 64,
        token_ciphertext=vault.encrypt('anthias_tok'),
    )


def _asset(name: str, order: int, **kwargs: Any) -> Asset:
    now = timezone.now()
    defaults: dict[str, Any] = {
        'name': name,
        'uri': f'https://example.com/{name}',
        'mimetype': 'webpage',
        'start_date': now - timedelta(days=1),
        'end_date': now + timedelta(days=30),
        'duration': 10,
        'is_enabled': True,
        'play_order': order,
    }
    defaults.update(kwargs)
    return Asset.objects.create(**defaults)


def _file_asset(name: str, order: int, data: bytes) -> Asset:
    from anthias_server.settings import settings

    asset = _asset(name, order, mimetype='image', uri='')
    path = os.path.join(settings['assetdir'], f'{asset.asset_id}.png')
    Path(path).write_bytes(data)
    asset.uri = path
    asset.save()
    return asset


@pytest.mark.django_db
def test_manifest_follows_play_order_and_skips_unplayable() -> None:
    _asset('b', 2)
    _asset('a', 1)
    _asset('busy', 0, is_processing=True)
    _asset('gone', 3, mimetype='image', uri='/nowhere/x.png')
    manifest = mirror.build_manifest()
    assert [i.name for i in manifest.items] == ['a', 'b']
    assert manifest.items[0].spec['uri'] == 'https://example.com/a'
    assert 'busy (still processing)' in manifest.skipped
    assert any(s.startswith('gone') for s in manifest.skipped)


@pytest.mark.django_db
def test_first_publish_creates_uploads_and_orders(node: Node) -> None:
    image = _file_asset('img', 0, b'png-bytes')
    _asset('web', 1)
    screen = FakeScreen()
    screen.assets['local1'] = {'asset_id': 'local1', 'name': 'added on screen'}

    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.ok, result.errors
    assert (result.created, result.deleted) == (2, 1)
    assert screen.uploads == 1
    assert 'local1' not in screen.assets
    names = [screen.assets[i]['name'] for i in screen.order]
    assert names == ['img', 'web']
    created_img = screen.assets[screen.order[0]]
    assert created_img['uri'].endswith('.tmp')
    assert created_img['ext'] == '.png'
    assert NodeAsset.objects.get(source_asset_id=image.asset_id)


@pytest.mark.django_db
def test_second_publish_is_a_no_op(node: Node) -> None:
    _file_asset('img', 0, b'png-bytes')
    _asset('web', 1)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())
    screen.calls.clear()

    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.unchanged == 2
    assert screen.writes() == []
    assert screen.uploads == 1


@pytest.mark.django_db
def test_setting_change_is_patched_in_place(node: Node) -> None:
    web = _asset('web', 0)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())
    remote_id = screen.order[0]

    web.duration = 42
    web.save()
    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.updated == 1
    assert screen.order == [remote_id]
    assert screen.assets[remote_id]['duration'] == 42


@pytest.mark.django_db
def test_edit_made_on_the_screen_is_reverted(node: Node) -> None:
    _asset('web', 0)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())
    remote_id = screen.order[0]
    screen.assets[remote_id]['is_enabled'] = False

    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.updated == 1
    assert screen.assets[remote_id]['is_enabled'] is True


@pytest.mark.django_db
def test_changed_file_or_url_replaces_the_remote_asset(node: Node) -> None:
    image = _file_asset('img', 0, b'one')
    web = _asset('web', 1)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())
    old_ids = list(screen.order)

    assert image.uri
    Path(image.uri).write_bytes(b'two, longer')
    web.uri = 'https://example.com/other'
    web.save()
    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert (result.created, result.deleted) == (2, 2)
    assert set(screen.order).isdisjoint(old_ids)
    assert screen.uploads == 2


@pytest.mark.django_db
def test_deleted_locally_is_deleted_on_screen(node: Node) -> None:
    keep = _asset('keep', 0)
    drop = _asset('drop', 1)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())

    drop.delete()
    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.deleted == 1
    assert [a['name'] for a in screen.assets.values()] == ['keep']
    assert list(
        NodeAsset.objects.values_list('source_asset_id', flat=True)
    ) == [keep.asset_id]


@pytest.mark.django_db
def test_remote_asset_deleted_on_screen_is_recreated(node: Node) -> None:
    _asset('web', 0)
    screen = FakeScreen()
    mirror.sync_node(node, screen, mirror.build_manifest())
    screen.assets.clear()

    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert result.created == 1
    assert len(screen.assets) == 1


@pytest.mark.django_db
def test_one_refused_asset_does_not_stop_the_rest(node: Node) -> None:
    _asset('bad', 0)
    _asset('good', 1)
    screen = FakeScreen()
    screen.fail_create_for = {'bad'}

    result = mirror.sync_node(node, screen, mirror.build_manifest())

    assert not result.ok
    assert 'bad' in result.errors[0]
    assert [a['name'] for a in screen.assets.values()] == ['good']


@pytest.mark.django_db
def test_asset_without_dates_is_sent_as_never_playing(node: Node) -> None:
    _asset('undated', 0, start_date=None, end_date=None)
    item = mirror.build_manifest().items[0]
    assert item.spec['start_date'].startswith('1970-01-01')
    assert item.spec['end_date'].startswith('1970-01-01')


# ---------------------------------------------------------------------------
# service.publish: state + audit


@pytest.mark.django_db
def test_publish_records_success(
    node: Node, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset('web', 0)
    screen = FakeScreen()
    monkeypatch.setattr(service, 'client_for', lambda _n: screen)

    service.publish(node, actor='operator')

    node.refresh_from_db()
    assert node.publish_state == PublishState.OK
    assert node.status == NodeStatus.ONLINE
    assert '1 added' in node.publish_detail
    entry = AuditEntry.objects.get(action='publish')
    assert (entry.outcome, entry.actor, entry.node_name) == (
        'ok',
        'operator',
        'Lobby',
    )


@pytest.mark.django_db
def test_publish_to_offline_screen_marks_it_offline(
    node: Node, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset('web', 0)

    class Offline(FakeScreen):
        def list_assets(self) -> list[dict[str, Any]]:
            raise NodeOfflineError('no route to host')

    monkeypatch.setattr(service, 'client_for', lambda _n: Offline())

    service.publish(node)

    node.refresh_from_db()
    assert node.status == NodeStatus.OFFLINE
    assert node.publish_state == PublishState.FAILED
    assert AuditEntry.objects.get(action='publish').outcome == 'failed'


@pytest.mark.django_db
def test_token_unreadable_on_this_device_means_left(node: Node) -> None:
    node.token_ciphertext = 'not-a-fernet-token'
    node.save()

    service.check_node(node)

    node.refresh_from_db()
    assert node.status == NodeStatus.UNAUTHORIZED


@pytest.mark.django_db
def test_publish_task_is_single_flight(
    node: Node, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anthias_server.controller import tasks

    calls: list[Any] = []
    monkeypatch.setattr(service, 'publish', lambda n, a: calls.append(n))
    requeued: list[Any] = []
    monkeypatch.setattr(
        tasks.publish_to_node,
        'apply_async',
        lambda args, countdown: requeued.append((args, countdown)),
    )
    from anthias_server.celery_tasks import r

    r.set(tasks.publish_lock_key(node.pk), 'held', nx=True)

    assert tasks.publish_to_node(node.pk) == 'waiting for the running publish'
    assert calls == []
    assert requeued == [((node.pk, ''), tasks.PUBLISH_RETRY_S)]

    r.delete(tasks.publish_lock_key(node.pk))
    tasks.publish_to_node(node.pk)
    assert len(calls) == 1
    assert r.get(tasks.publish_lock_key(node.pk)) is None
