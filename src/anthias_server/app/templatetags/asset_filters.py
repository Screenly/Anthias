"""Template filters for the asset list page.

`to_json` serialises an Asset (or any model instance) to a JSON string
safe for inlining into an Alpine `@click` handler. The form-modal opens
edit mode by reading these inline blobs rather than refetching from the
API — keeps the row markup self-contained.
"""

import json
from datetime import date, datetime, time
from typing import Any

from django.template import Library
from django.utils.safestring import SafeString, mark_safe
from django.utils import timezone

register = Library()


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, '_meta'):
        out: dict[str, Any] = {}
        for field in obj._meta.fields:
            value = getattr(obj, field.name)
            out[field.name] = _coerce(value)
        # Pre-format the local-time strings the edit modal binds to so
        # the template doesn't re-do the timezone math in JS — keeps
        # parity with the React EditAssetModal which used Intl.
        if hasattr(obj, 'start_date') and obj.start_date:
            out['start_date_local'] = timezone.localtime(
                obj.start_date
            ).strftime('%Y-%m-%dT%H:%M')
        if hasattr(obj, 'end_date') and obj.end_date:
            out['end_date_local'] = timezone.localtime(obj.end_date).strftime(
                '%Y-%m-%dT%H:%M'
            )
        return out
    return _coerce(obj)


def _coerce(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


@register.filter
def to_json(value: Any) -> SafeString:
    """Render an Asset (or any model) as a JSON literal for Alpine."""
    encoded = json.dumps(
        _to_dict(value),
        default=str,
        separators=(',', ':'),
    )
    # mark_safe lets `'` survive HTML autoescaping; we still hex-encode
    # the apostrophe and ampersand below to keep the literal valid as
    # a JS string inside an `x-on:click="openEdit(...)"` attribute.
    safe = (
        encoded.replace('&', '\\u0026')
        .replace("'", '\\u0027')
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
    )
    return mark_safe(safe)
