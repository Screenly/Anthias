"""One-time repair of double-encoded UTF-8 in existing ``Asset.name``.

A misbehaving uploader that double-encodes a filename stores e.g.
``FormulÃ¡rios`` instead of ``Formulários`` (the UTF-8 bytes of ``á``
read back as the Latin-1 chars ``Ã`` + ``¡``). Anthias never produces
this itself, but it stored whatever the request body carried, so
already-uploaded assets keep the garbled name in the UI and in the
viewer's ``Showing asset …`` log line. ``Asset.save`` now repairs new
writes; this migration fixes the rows that pre-date that guardrail.

The repair logic is inlined rather than imported from
``anthias_server.app.models`` on purpose — migrations are frozen
snapshots of intent, and a future change to the model helper must not
retroactively alter what this one-time data fix did.

Idempotent: a name is rewritten only when every character is in the
Latin-1 range *and* those bytes form a valid UTF-8 string that differs
from the input — a strong heuristic for double-encoded UTF-8, though not
a proof (a genuinely Latin-1 name whose bytes are also valid UTF-8, e.g.
``Â©`` → ``©``, is indistinguishable and gets rewritten too; such
collisions are vanishingly rare in real filenames). Correctly stored
names (``Formulários``, ``Café``, ``日本語``) raise on the encode or
decode step and are left untouched, so a re-run changes nothing.
"""

from __future__ import annotations

from django.db import migrations


def _repair_mojibake(text):  # type: ignore[no-untyped-def]
    if not text:
        return text
    try:
        repaired = text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired != text else text


def _repair_names(apps, schema_editor):  # type: ignore[no-untyped-def]
    asset_model = apps.get_model('anthias_app', 'Asset')
    # ``.only()`` + ``.iterator()`` streams rows in chunks instead of
    # caching the whole table in memory; per-row ``save`` still works.
    rows = (
        asset_model.objects.exclude(name__isnull=True)
        .only('asset_id', 'name')
        .iterator()
    )
    for asset in rows:
        repaired = _repair_mojibake(asset.name)
        if repaired != asset.name:
            asset.name = repaired
            asset.save(update_fields=['name'])


class Migration(migrations.Migration):
    dependencies = [
        ('anthias_app', '0006_asset_metadata'),
    ]

    operations = [
        migrations.RunPython(
            _repair_names, reverse_code=migrations.RunPython.noop
        ),
    ]
