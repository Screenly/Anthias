"""Tests for how the upload chunk size is configured.

The value reaches Django from anthias.conf, then the
ANTHIAS_UPLOAD_CHUNK_SIZE_MB env var, then a default — and reaches the
browser through a <meta> tag. It is set once and rarely looked at
again, typically through a balena dashboard field or a hand-edited
config, so no value it can be given may raise: on a headless device
the container would simply never come up.
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from anthias_server.django_project.settings import (
    UPLOAD_CHUNK_SIZE_MB_DEFAULT,
    UPLOAD_CHUNK_SIZE_MB_MAX,
    UPLOAD_CHUNK_SIZE_MB_MIN,
    get_configured_upload_chunk_size_mb,
    resolve_upload_chunk_size_mb,
)
from anthias_server.settings import DEFAULTS

CHUNKING_TS = (
    Path(__file__).resolve().parents[1]
    / 'src/anthias_server/app/static/src/home/chunking.ts'
)


@pytest.fixture(autouse=True)
def _empty_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Point HOME at an empty directory.

    The config file is the first rung, so without this every env-var
    case below fails on the machine of any developer who followed the
    FAQ and set `upload_chunk_size_mb` in their own anthias.conf. CI
    never sees it — a container's config has no such key.
    """
    monkeypatch.setenv('HOME', str(tmp_path))
    yield


def _resolve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conf: str | None,
    env: str | None,
) -> int:
    if conf is not None:
        (tmp_path / '.anthias').mkdir(exist_ok=True)
        (tmp_path / '.anthias' / 'anthias.conf').write_text(
            f'[main]\nupload_chunk_size_mb = {conf}\n'
        )
    if env is None:
        monkeypatch.delenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', raising=False)
    else:
        monkeypatch.setenv('ANTHIAS_UPLOAD_CHUNK_SIZE_MB', env)
    return resolve_upload_chunk_size_mb()


D = UPLOAD_CHUNK_SIZE_MB_DEFAULT


@pytest.mark.parametrize(
    ('conf', 'env', 'expected', 'why'),
    [
        # Precedence. The config file wins because it is the rung that
        # survives an upgrade; the env var is how balena sets it.
        ('4', '20', 4, 'config beats the environment'),
        ('', '20', 20, 'a blank config defers to the environment'),
        (None, '20', 20, 'no config at all defers to the environment'),
        (None, None, D, 'neither set'),
        # Parsing. Either rung may hold anything a human can type.
        (None, '8', 8, 'a plain value'),
        (None, ' 8 ', 8, 'surrounding whitespace'),
        # MB is decimal, and this knob exists to get UNDER a proxy cap,
        # so 8.5 must not be rounded up or fall back to 16.
        (None, '8.5', 8, 'a fractional value rounds down'),
        ('2.9', None, 2, 'and does so from the config too'),
        # Unparseable falls back rather than raising.
        (None, '16m', D, 'a stray unit'),
        (None, 'sixteen', D, 'not a number'),
        (None, '0x10', D, 'not decimal'),
        (None, 'nan', D, 'nan'),
        (None, 'inf', D, 'inf'),
        (None, '9' * 5000, D, 'longer than int() will parse'),
        # docker-compose.yml.tmpl always sets the variable, so an
        # operator who has chosen nothing sends an empty string.
        (None, '', D, 'empty, which compose always renders'),
        (None, '   ', D, 'whitespace only'),
        # Clamped both ways.
        (None, '32', UPLOAD_CHUNK_SIZE_MB_MAX, 'above the ceiling'),
        (None, '0', UPLOAD_CHUNK_SIZE_MB_MIN, 'below the floor'),
        (None, '-4', UPLOAD_CHUNK_SIZE_MB_MIN, 'negative'),
        ('512', None, UPLOAD_CHUNK_SIZE_MB_MAX, 'clamped from the config'),
    ],
)
def test_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conf: str | None,
    env: str | None,
    expected: int,
    why: str,
) -> None:
    assert _resolve(monkeypatch, tmp_path, conf, env) == expected, why


@pytest.mark.parametrize('value', ['16m', '32'])
def test_a_value_it_cannot_use_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    """Reported as written, not as parsed: 0.5 logged as "0 is outside
    [1, 24]" sends the operator looking for a zero they never typed."""
    with caplog.at_level('WARNING'):
        _resolve(monkeypatch, tmp_path, None, value)
    assert value in caplog.text


@pytest.mark.parametrize('kind', ['fifo', 'directory', 'binary'])
def test_an_unreadable_config_is_not_an_error(
    tmp_path: Path, kind: str
) -> None:
    """Read at startup, so a read that blocks or throws means the
    container never comes up rather than one bad page."""
    target = tmp_path / 'anthias.conf'
    if kind == 'fifo':
        os.mkfifo(target)
    elif kind == 'directory':
        target.mkdir()
    else:
        target.write_bytes(bytes(range(256)) * 4)

    assert get_configured_upload_chunk_size_mb(str(target)) == ''


def test_the_config_file_carries_it_across_an_upgrade() -> None:
    """upgrade_containers.sh regenerates docker-compose.yml from its
    template every run, so an env var set on the host is not
    persistence. anthias.conf is on the mounted volume."""
    assert 'upload_chunk_size_mb' in DEFAULTS['main']


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('MAX_CHUNK_MB', UPLOAD_CHUNK_SIZE_MB_MAX),
        ('MIN_CHUNK_MB', UPLOAD_CHUNK_SIZE_MB_MIN),
        ('FALLBACK_CHUNK_MB', UPLOAD_CHUNK_SIZE_MB_DEFAULT),
    ],
)
def test_browser_bounds_match_the_server(name: str, expected: int) -> None:
    """chunking.ts cannot import a Python constant, so the two sets are
    written out twice. Drift resurrects the problem the server-side
    clamp was added to prevent: a stale browser ceiling silently
    overriding the value the server advertised."""
    match = re.search(
        rf'^const {name} = (\d+)$', CHUNKING_TS.read_text(), re.MULTILINE
    )
    assert match is not None, f'{name} not found in chunking.ts'
    assert int(match.group(1)) == expected
