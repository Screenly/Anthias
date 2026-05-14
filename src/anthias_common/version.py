"""Project version lookup for code that must stay layer-agnostic.

Lives in ``anthias_common`` so both ``anthias_server`` (System Info
HTML, v2 info API) and ``anthias_common.http`` (outbound User-Agent)
can read the CalVer release without reaching into Django-land. The
old ``anthias_server.lib.diagnostics.get_anthias_release`` re-exports
from here for backwards compatibility — nothing else lived in that
function, so the move is a pure relocation.
"""

from __future__ import annotations


def get_anthias_release() -> str:
    """Read the project version, sourced from pyproject.toml's
    [project].version (currently CalVer ``YYYY.M.MICRO``).

    Resolution order:
      1. ``importlib.metadata.version('anthias')`` — works for
         editable installs (``pip install -e .``) and any path where
         the project ships as a wheel.
      2. Fallback: parse ``pyproject.toml`` directly with ``tomllib``.
         Required because every production / test / host environment
         runs ``uv sync --no-install-project`` (see
         docker/uv-builder.j2, docker/Dockerfile.{server,test,viewer},
         bin/install.sh) — that flag installs the project's deps but
         NOT the project itself, so importlib.metadata has no record
         of an ``anthias`` distribution to read.

    Cached after first successful read so the System Info HTML render
    (called per request) and the v2 info API don't re-open the file.
    Returns the empty string only when both sources fail.
    """
    cached: str | None = getattr(get_anthias_release, '_cached', None)
    if cached is not None:
        return cached
    value: str
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version('anthias')
        except PackageNotFoundError:
            value = _read_version_from_pyproject()
    except Exception:
        value = ''
    setattr(get_anthias_release, '_cached', value)
    return value


def _read_version_from_pyproject() -> str:
    """Last-ditch source for the project version when the package
    isn't installed in the active venv. Walks up from this file to
    find the repo-root pyproject.toml — ``__file__`` lives at
    ``src/anthias_common/version.py``, so parents[2] is the checkout
    root in both editable installs and the ``uv sync
    --no-install-project`` Docker layout."""
    try:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        with pyproject.open('rb') as f:
            data = tomllib.load(f)
        return str(data.get('project', {}).get('version', ''))
    except Exception:
        return ''
