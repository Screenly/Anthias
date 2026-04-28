import sys

# `pydbus` (a viewer dependency) does `from gi.repository import GLib`
# (and Gio, GObject) at import time, plus `from gi.repository.GLib
# import Variant`. PyGObject (`gi`) lives in the Anthias container
# images as the apt package `python3-gi` but isn't installed on the
# host GitHub Actions runner where unit tests now run. Compiling
# PyGObject from source on every CI run just to import a module no
# host unit test exercises (none of them talk to D-Bus) isn't worth
# the time.
#
# If `gi` is genuinely importable (Docker integration tests), do
# nothing and let the real package stay in use. Otherwise stub each
# submodule pydbus references — registering them individually in
# sys.modules so Python's `from a.b import C` resolves cleanly
# instead of complaining that `a.b` (a MagicMock) isn't a package.
# Tests that touch pydbus already mock `pydbus.SessionBus`, so the
# stubbed `gi` is never observed at runtime.
try:
    import gi  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock

    for _name in (
        'gi',
        'gi.repository',
        'gi.repository.GLib',
        'gi.repository.Gio',
        'gi.repository.GObject',
    ):
        sys.modules[_name] = MagicMock()
    del _name
