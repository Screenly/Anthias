"""One build, one release string.

``get_anthias_release`` has two sources — the installed distribution's
metadata and, when the project isn't installed in the active venv,
pyproject.toml read directly. setuptools normalises the version on the
way into package metadata and tomllib does not, so a zero-padded
CalVer month used to come back as ``2026.9.0`` from one source and
``2026.09.0`` from the other. Sentry keys releases by string and
recorded both for a single image, which is enough to break "resolved
in release X" and the regression detection built on it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest import mock

import pytest

from anthias_common import version as v


def clear_release_cache() -> None:
    """``get_anthias_release`` memoises on a function attribute."""
    if hasattr(v.get_anthias_release, '_cached'):
        delattr(v.get_anthias_release, '_cached')


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Clear the memo on the way in and out, so neither a mocked
    value nor a real one leaks between tests."""
    clear_release_cache()
    yield
    clear_release_cache()


class TestCanonicalize:
    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            ('2026.09.0', '2026.9.0'),
            ('2026.06.3', '2026.6.3'),
            ('2026.08.2', '2026.8.2'),
            # Already canonical, and the months that need no padding.
            ('2026.9.0', '2026.9.0'),
            ('2026.10.0', '2026.10.0'),
            ('2026.5.0', '2026.5.0'),
            # A zero component is a zero, not an empty string.
            ('2026.0.0', '2026.0.0'),
            ('0', '0'),
        ],
    )
    def test_strips_padding(self, raw: str, expected: str) -> None:
        assert v._canonicalize(raw) == expected

    @pytest.mark.parametrize(
        'raw',
        [
            '',
            'dev-snapshot',
            # Not our CalVer shape: report it verbatim rather than
            # guess at a normalisation that may not apply.
            '2026.09.0rc1',
            '2026.09.0+abc1234',
            'v2026.09.0',
        ],
    )
    def test_leaves_anything_else_alone(self, raw: str) -> None:
        assert v._canonicalize(raw) == raw


class TestGetAnthiasRelease:
    def test_installed_metadata_wins(self) -> None:
        with mock.patch.object(
            sys.modules['importlib.metadata'],
            'version',
            return_value='2026.9.0',
        ):
            assert v.get_anthias_release() == '2026.9.0'

    def test_pyproject_fallback_is_canonicalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The branch that diverged: tomllib hands back the padded
        string, and without normalisation it reached Sentry as a
        release of its own."""
        monkeypatch.setattr(
            v, '_read_version_from_pyproject', lambda: '2026.09.0'
        )
        with mock.patch.object(
            sys.modules['importlib.metadata'],
            'version',
            side_effect=sys.modules['importlib.metadata'].PackageNotFoundError,
        ):
            assert v.get_anthias_release() == '2026.9.0'

    def test_both_sources_agree_on_one_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property that matters: whichever source answers, the
        same pyproject version produces the same release string."""
        meta = sys.modules['importlib.metadata']
        with mock.patch.object(meta, 'version', return_value='2026.9.0'):
            from_metadata = v.get_anthias_release()
        clear_release_cache()
        monkeypatch.setattr(
            v, '_read_version_from_pyproject', lambda: '2026.09.0'
        )
        with mock.patch.object(
            meta, 'version', side_effect=meta.PackageNotFoundError
        ):
            from_pyproject = v.get_anthias_release()
        assert from_metadata == from_pyproject == '2026.9.0'

    def test_empty_when_both_sources_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(v, '_read_version_from_pyproject', lambda: '')
        meta = sys.modules['importlib.metadata']
        with mock.patch.object(
            meta, 'version', side_effect=meta.PackageNotFoundError
        ):
            assert v.get_anthias_release() == ''
