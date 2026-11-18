"""Project version lookup for code that must stay layer-agnostic.

Lives in ``anthias_common`` so both ``anthias_server`` (System Info
HTML, v2 info API) and ``anthias_common.http`` (outbound User-Agent)
can read the CalVer release without reaching into Django-land. The
old ``anthias_server.lib.diagnostics.get_anthias_release`` re-exports
from here for backwards compatibility — nothing else lived in that
function, so the move is a pure relocation.
"""

from __future__ import annotations

import re

# An all-numeric dotted version, the ``YYYY.MM.MICRO`` shape this
# project's CalVer takes. Deliberately ``[0-9]`` rather than ``\d``,
# which also matches non-ASCII digits.
_NUMERIC_VERSION = re.compile(r'[0-9]+(?:\.[0-9]+)*')


def _canonicalize(value: str) -> str:
    """Drop leading zeros from an all-numeric dotted version.

    The release CalVer is ``YYYY.0M.MICRO`` and pyproject.toml carries
    it zero-padded on purpose, matching the git tag (``v2026.09.0``).
    Consumers that need strict semver already unpad it: uv.lock stores
    ``2026.9.0``, and ``bin/render_balena_yml.sh`` normalises the same
    way before stamping balena.yml, which rejects a padded segment.

    Only one of the two branches below unpads it, though. The build
    backend applies PEP 440 normalisation when it copies the version
    into the package metadata, so ``importlib.metadata`` reports
    ``2026.9.0``; reading pyproject.toml ourselves yields the padded
    string verbatim. One build therefore described itself two ways
    depending on whether the distribution happened to be installed in
    the active venv.

    Sentry is where that bites. It keys releases by string, so the
    padded month splits a single image into two unrelated releases —
    ``2026.9.0+fa2d90a`` and ``2026.09.0+fa2d90a`` are both on record
    for one build — and "resolved in release X", along with the
    regression detection built on top of it, only ever sees whichever
    half an event landed in. The same split shows up, more
    cosmetically, in the System Info page, the v2 info API and the
    outbound User-Agent.

    Unpadding here puts both branches on the side the rest of the
    toolchain already takes, and it is the whole of what PEP 440
    normalisation does to a version of this shape. Anything that is
    not an all-numeric dotted version is returned untouched: a string
    we do not recognise is better reported verbatim than guessed at.
    """
    if not _NUMERIC_VERSION.fullmatch(value):
        return value
    return '.'.join(str(int(part)) for part in value.split('.'))


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

    Either source is put through ``_canonicalize`` so both describe
    the same build identically — see there for why that is not
    cosmetic.

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
    value = _canonicalize(value)
    get_anthias_release._cached = value  # type: ignore[attr-defined]
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
