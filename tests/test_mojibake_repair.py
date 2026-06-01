"""Tests for the double-encoded-UTF-8 (mojibake) repair on asset names.

Covers the pure helper, the write-side ``Asset.save`` guardrail (which
catches new uploads from any API version or the web form), and the
data-migration logic that fixes rows stored before the guardrail
existed.
"""

import pytest

from anthias_server.app.models import Asset, repair_mojibake

# ``Formulários`` round-tripped through the classic ``UTF-8 bytes read
# as Latin-1`` corruption. Kept as the canonical mojibake fixture so the
# intent is obvious without sprinkling non-ASCII escapes through the
# assertions.
GARBLED = 'Formulários'.encode('utf-8').decode('latin-1')


@pytest.mark.parametrize(
    'given, expected',
    [
        # Genuine mojibake — the one case we repair.
        (GARBLED, 'Formulários'),
        # Correctly-encoded text must survive untouched: a multi-byte
        # accent, a name with several Latin-1 accents, and CJK each
        # raise on the encode or decode step and short-circuit.
        ('Formulários', 'Formulários'),
        ('Café Über señor', 'Café Über señor'),
        ('日本語', '日本語'),
        # ASCII / empty / None are no-ops.
        ('Plain Name 2', 'Plain Name 2'),
        ('', ''),
        (None, None),
        # A lone Latin-1 lead byte is not valid UTF-8 once re-decoded,
        # so it is left alone rather than mangled.
        ('Ã', 'Ã'),
        # Documented false positive: a genuinely Latin-1 ``Â©`` (U+00C2
        # U+00A9) has bytes that are also valid UTF-8 (``©``), so it is
        # indistinguishable from mojibake and gets rewritten. Accepted
        # trade-off — see ``repair_mojibake``'s docstring.
        ('Â©', '©'),
    ],
)
def test_repair_mojibake(given: str | None, expected: str | None) -> None:
    assert repair_mojibake(given) == expected


def test_repair_mojibake_is_idempotent() -> None:
    once = repair_mojibake(GARBLED)
    assert repair_mojibake(once) == once == 'Formulários'


@pytest.mark.django_db
def test_save_repairs_mojibake_name() -> None:
    asset = Asset.objects.create(name=GARBLED, mimetype='image')
    asset.refresh_from_db()
    assert asset.name == 'Formulários'


@pytest.mark.django_db
def test_save_leaves_clean_name_untouched() -> None:
    asset = Asset.objects.create(name='Café Über señor', mimetype='image')
    asset.refresh_from_db()
    assert asset.name == 'Café Über señor'


@pytest.mark.django_db
def test_migration_repairs_existing_rows() -> None:
    """The migration's repair pass fixes pre-existing garbled rows.

    ``Asset.save`` now cleans names on write, so to exercise the
    migration's own logic against a *stored* mojibake row we write the
    column directly with ``QuerySet.update`` (which bypasses ``save``).
    """
    import importlib

    migration = importlib.import_module(
        'anthias_server.app.migrations.0007_repair_mojibake_asset_names'
    )

    asset = Asset.objects.create(name='placeholder', mimetype='image')
    Asset.objects.filter(pk=asset.pk).update(name=GARBLED)

    class _Apps:
        @staticmethod
        def get_model(app_label: str, model_name: str) -> type[Asset]:
            return Asset

    migration._repair_names(_Apps(), None)

    asset.refresh_from_db()
    assert asset.name == 'Formulários'
