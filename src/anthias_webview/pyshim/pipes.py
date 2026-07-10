"""Shim for the stdlib ``pipes`` module, removed in Python 3.13 (Debian
trixie). Chromium 87's build tooling (build/android/gyp/util/build_utils.py)
uses only ``pipes.quote``, which moved to ``shlex.quote``.
"""

from shlex import quote  # noqa: F401
