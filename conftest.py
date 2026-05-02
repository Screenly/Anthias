"""
Root-level pytest configuration.

This file enables the unit-test suite to run on a developer's host
without Docker, without a Redis server, and without the system PyGObject
stack the viewer service normally requires. Integration tests
(``-m integration``) opt back into real services by replacing these
fixtures themselves.

Three concerns are handled here, in order:

1. Force ``ENVIRONMENT=test`` so ``anthias_django/settings.py`` selects
   the SQLite test-DB branch (a repo-local path under ``BASE_DIR`` by
   default; CI overrides via ``ANTHIAS_TEST_DB_PATH``).

2. Stub ``gi`` / ``gi.repository`` / ``pydbus`` in ``sys.modules`` *before*
   any application module is imported. ``viewer/__init__.py`` does
   ``import pydbus`` at module load, and ``pydbus`` in turn imports
   ``gi.repository.Gio`` — which only resolves on hosts with the
   distribution's ``python3-gi`` package installed and wired into the
   active interpreter. The stubs let the import succeed; tests that
   exercise dbus paths mock the relevant calls themselves.

3. Replace ``lib.utils.connect_to_redis`` with a dict-backed
   ``MagicMock`` factory before any test module imports it, then expose
   the same fake via an autouse fixture. Several modules call
   ``r = connect_to_redis()`` at import time
   (``celery_tasks``, ``lib.github``, ``lib.telemetry``, ...); patching
   the factory once at conftest load time means the module-level ``r``
   bindings hold a fake, not a client pointed at host ``redis``.
"""

import importlib.util
import os
import sys
import types
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1. ENVIRONMENT=test (settings.py reads this at import time)
# ---------------------------------------------------------------------------

os.environ.setdefault('ENVIRONMENT', 'test')


# ---------------------------------------------------------------------------
# 2. Stub gi / gi.repository / pydbus before viewer modules are loaded
# ---------------------------------------------------------------------------


def _install_dbus_stubs() -> None:
    """
    Insert stand-in modules so ``import pydbus`` (and its transitive
    ``from gi.repository import Gio, GLib, GObject``) succeeds on
    hosts that don't have the full PyGObject + pydbus stack.

    Three host shapes show up in the wild:

    1. Both ``gi`` and ``pydbus`` available (target image): no-op.
    2. ``gi`` missing entirely (typical dev laptop without
       ``python3-gi`` apt package): stub both, because the real
       pydbus's module-load does ``from gi.repository import
       GLib, GObject`` and our minimal gi stub can't satisfy that.
    3. ``gi`` present but ``pydbus`` not pip-installed: stub only
       pydbus.
    """
    gi_missing = importlib.util.find_spec('gi') is None
    pydbus_missing = importlib.util.find_spec('pydbus') is None

    if gi_missing:
        gi_module = types.ModuleType('gi')
        gi_repository = types.ModuleType('gi.repository')
        # MagicMock for the GLib/Gio/GObject surface — pydbus only
        # touches these when a bus is actually constructed (e.g.
        # ``pydbus.SessionBus()`` inside a function body); tests that
        # hit those paths mock them themselves.
        setattr(gi_repository, 'Gio', MagicMock(name='gi.repository.Gio'))
        setattr(gi_repository, 'GLib', MagicMock(name='gi.repository.GLib'))
        setattr(
            gi_repository, 'GObject', MagicMock(name='gi.repository.GObject')
        )
        setattr(gi_module, 'repository', gi_repository)
        sys.modules['gi'] = gi_module
        sys.modules['gi.repository'] = gi_repository

    if pydbus_missing or gi_missing:
        pydbus_module = types.ModuleType('pydbus')
        setattr(
            pydbus_module, 'SessionBus', MagicMock(name='pydbus.SessionBus')
        )
        setattr(pydbus_module, 'SystemBus', MagicMock(name='pydbus.SystemBus'))
        sys.modules['pydbus'] = pydbus_module


_install_dbus_stubs()


# ---------------------------------------------------------------------------
# 3. Defensive Redis mock — replace connect_to_redis() everywhere
# ---------------------------------------------------------------------------


def _make_fake_redis() -> MagicMock:
    """
    A dict-backed Redis mock matching the surface our code uses.

    String ops (get/set/delete/expire/exists/flushdb/publish) and list
    ops (rpush/lpop/blpop) are modelled on the real Redis semantics so
    test paths that exercise both — notably ``ReplyCollector.recv_json``
    via BLPOP — see realistic behaviour rather than no-ops.
    """
    store: dict[str, Any] = {}

    fake = MagicMock(name='FakeRedis')
    fake.get.side_effect = store.get
    fake.set.side_effect = lambda key, value: store.__setitem__(key, value)
    fake.delete.side_effect = lambda *keys: [store.pop(k, None) for k in keys]
    fake.expire.side_effect = lambda key, _ttl: bool(key in store)
    fake.exists.side_effect = lambda *keys: sum(1 for k in keys if k in store)
    fake.flushdb.side_effect = lambda: store.clear()
    fake.publish.side_effect = lambda channel, msg: 0

    def _rpush(key: str, *values: Any) -> int:
        bucket = store.setdefault(key, [])
        bucket.extend(values)
        return len(bucket)

    def _lpop(key: str) -> Any:
        bucket = store.get(key)
        if not bucket:
            return None
        value = bucket.pop(0)
        if not bucket:
            store.pop(key, None)
        return value

    def _blpop(keys: Any, timeout: float | None = None) -> Any:
        # Real BLPOP blocks until a value is available or the timeout
        # expires. Tests don't drive a writer thread, so block-then-
        # poll behaviour collapses to "return immediately": yield the
        # first available value, or None if every key is empty.
        if isinstance(keys, (str, bytes)):
            keys = [keys]
        for key in keys:
            value = _lpop(key)
            if value is not None:
                return (key, value)
        return None

    fake.rpush.side_effect = _rpush
    fake.lpop.side_effect = _lpop
    fake.blpop.side_effect = _blpop
    return fake


# Patch ``connect_to_redis`` at the source so the module-level
# ``r = connect_to_redis()`` bindings in celery_tasks / lib.github /
# lib.telemetry / api.views.mixins / viewer / etc. all resolve to the
# fake the moment those modules are first imported.
_SESSION_FAKE_REDIS = _make_fake_redis()


def _patch_connect_to_redis() -> None:
    import lib.utils as _lib_utils

    _lib_utils.connect_to_redis = lambda: _SESSION_FAKE_REDIS


_patch_connect_to_redis()


@pytest.fixture(scope='session', autouse=True)
def _ensure_assetdir() -> None:
    """
    Some legacy fixtures (e.g. ``api/tests/test_v1_endpoints.py::
    cleanup_asset_dir``) iterate ``settings['assetdir']`` during
    teardown without creating it first. The Docker test image creates
    ``/data/anthias_assets`` in its build (see
    ``docker/Dockerfile.test.j2``); local hosts have no such guarantee.
    Materialise the path once per session so those fixtures don't
    ``FileNotFoundError`` out before the test even runs.
    """
    from settings import settings as _anthias_settings

    asset_dir = _anthias_settings.get('assetdir')
    if asset_dir:
        os.makedirs(asset_dir, exist_ok=True)


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """
    Replace ``lib.utils.connect_to_redis`` with a dict-backed
    ``MagicMock`` for every test, including any module-level
    ``r = connect_to_redis()`` bindings that fixtures import indirectly.

    Tests that need their own Redis mock (e.g. ``tests/test_telemetry.py``,
    ``tests/test_messaging.py``) override this by patching ``module.r``
    directly — that takes precedence inside the per-test setup chain.
    """
    fake = _make_fake_redis()
    monkeypatch.setattr('lib.utils.connect_to_redis', lambda: fake)

    # Replace already-bound ``r`` attributes on modules that called
    # connect_to_redis() at import time. Only modules already in
    # sys.modules are touched — others get the fake on first import via
    # the conftest-level patch above.
    for module_path in (
        'anthias_app.views',
        'api.views.mixins',
        'api.views.v2',
        'celery_tasks',
        'lib.github',
        'lib.telemetry',
        'viewer',
    ):
        mod = sys.modules.get(module_path)
        if mod is not None and hasattr(mod, 'r'):
            monkeypatch.setattr(f'{module_path}.r', fake)

    yield fake
