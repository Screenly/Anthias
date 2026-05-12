"""
Root-level pytest configuration.

This file enables the unit-test suite to run on a developer's host
without Docker, without a Redis server, and without the system PyGObject
stack the viewer service normally requires. Integration tests
(``-m integration``) opt back into real services by replacing these
fixtures themselves.

Three concerns are handled here, in order:

1. Force ``ENVIRONMENT=test`` so ``anthias_server.django_project/settings.py`` selects
   the SQLite test-DB branch (a repo-local path under ``BASE_DIR`` by
   default; CI overrides via ``ANTHIAS_TEST_DB_PATH``).

2. Stub ``gi`` / ``gi.repository`` / ``pydbus`` in ``sys.modules`` *before*
   any application module is imported. ``viewer/__init__.py`` does
   ``import pydbus`` at module load, and ``pydbus`` in turn imports
   ``gi.repository.Gio`` — which only resolves on hosts with the
   distribution's ``python3-gi`` package installed and wired into the
   active interpreter. The stubs let the import succeed; tests that
   exercise dbus paths mock the relevant calls themselves.

3. Replace ``anthias_common.utils.connect_to_redis`` with a dict-backed
   ``MagicMock`` factory before any test module imports it, then expose
   the same fake via an autouse fixture. Several modules call
   ``r = connect_to_redis()`` at import time
   (``anthias_server.celery_tasks``, ``anthias_server.lib.github``, ``anthias_server.lib.telemetry``, ...); patching
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

    def _set(
        key: str,
        value: Any,
        *,
        nx: bool = False,
        ex: int | None = None,
        **_: Any,
    ) -> bool | None:
        # Match real Redis ``SET ... NX``: succeeds only if the key is
        # not already present, returns True on success / None on no-op.
        # Tests for SETNX-based gates (per-asset recheck cooldown,
        # sweep singleton lock, splash IP-refresh debounce) rely on
        # this — without it, every "is the lock held?" check would
        # spuriously believe the lock was free.
        if nx and key in store:
            return None
        store[key] = value
        return True

    fake.set.side_effect = _set
    fake.delete.side_effect = lambda *keys: sum(
        1 for k in keys if store.pop(k, None) is not None
    )
    fake.expire.side_effect = lambda key, _ttl: bool(key in store)
    fake.exists.side_effect = lambda *keys: sum(1 for k in keys if k in store)
    fake.flushdb.side_effect = lambda: store.clear()
    fake.publish.side_effect = lambda channel, msg: 0

    def _eval(script: str, numkeys: int, *args: Any) -> Any:
        # Compare-and-delete is the only ``EVAL`` script in the
        # codebase (sweep lock release in anthias_server.celery_tasks.py). Implement
        # that pattern directly; any other script becomes a no-op.
        if "redis.call('get', KEYS[1])" in script and "'del'" in script:
            keys = list(args[:numkeys])
            argv = list(args[numkeys:])
            if keys and argv and store.get(keys[0]) == argv[0]:
                store.pop(keys[0], None)
                return 1
            return 0
        return None

    fake.eval.side_effect = _eval

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
# ``r = connect_to_redis()`` bindings in anthias_server.celery_tasks / anthias_server.lib.github /
# anthias_server.lib.telemetry / api.views.mixins / viewer / etc. all resolve to the
# fake the moment those modules are first imported.
_SESSION_FAKE_REDIS = _make_fake_redis()


def _patch_connect_to_redis() -> None:
    import anthias_common.utils as _lib_utils

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
    from anthias_server.settings import settings as _anthias_settings

    asset_dir = _anthias_settings.get('assetdir')
    if asset_dir:
        os.makedirs(asset_dir, exist_ok=True)


# Browser-test failure artifacts are owned by pytest-playwright. The
# `--tracing retain-on-failure --screenshot only-on-failure
# --output test-artifacts` flags in pyproject.toml's addopts make it
# write `<output>/<test-id>/{trace.zip,test-failed-1.png}` for failed
# tests and nothing for passing ones. The GH Actions
# upload-artifact@v7 step in test-runner.yml uploads the directory on
# job failure, where the trace replays via `playwright show-trace`.


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Set ``DJANGO_ALLOW_ASYNC_UNSAFE=1`` only when the run actually
    contains integration tests.

    Playwright's sync API spins up an internal asyncio loop to talk
    to the browser over the CDP socket; Django's ORM detects that
    loop and refuses sync calls (``SynchronousOnlyOperation``) unless
    this flag is set. The single-threaded test process never invokes
    ORM and Playwright concurrently, so the safety net Django enforces
    isn't doing useful work for this suite.

    Setting it process-wide unconditionally would also disable the
    safety net for unit tests, where an accidental ORM call from
    inside an event loop is a real bug we want Django to flag. Hooking
    at collection time means a unit-only run (``pytest -m "not
    integration"``) leaves the variable unset and the check active;
    a run that includes integration tests sets it once before
    pytest-django's DB setup (which would otherwise hit the same
    check itself).
    """
    if any('integration' in item.keywords for item in items):
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = '1'


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """
    Replace ``anthias_common.utils.connect_to_redis`` with a dict-backed
    ``MagicMock`` for every test, including any module-level
    ``r = connect_to_redis()`` bindings that fixtures import indirectly.

    Tests that need their own Redis mock (e.g. ``tests/test_telemetry.py``,
    ``tests/test_messaging.py``) override this by patching ``module.r``
    directly — that takes precedence inside the per-test setup chain.
    """
    fake = _make_fake_redis()
    monkeypatch.setattr('anthias_common.utils.connect_to_redis', lambda: fake)

    # Replace already-bound ``r`` attributes on modules that called
    # connect_to_redis() at import time. Only modules already in
    # sys.modules are touched — others get the fake on first import via
    # the conftest-level patch above.
    for module_path in (
        'anthias_server.app.views',
        'anthias_server.api.views.mixins',
        'anthias_server.api.views.v2',
        'anthias_server.celery_tasks',
        'anthias_server.lib.github',
        'anthias_server.lib.telemetry',
        'anthias_viewer',
    ):
        mod = sys.modules.get(module_path)
        if mod is not None and hasattr(mod, 'r'):
            monkeypatch.setattr(f'{module_path}.r', fake)

    yield fake
