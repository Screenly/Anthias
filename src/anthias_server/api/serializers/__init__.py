from datetime import timezone
from os import path
from typing import Any

from rest_framework.exceptions import ValidationError
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    Field,
    IntegerField,
    ModelSerializer,
    Serializer,
)

from anthias_server.app.models import Asset, DURATION_S_MAX
from anthias_common.utils import validate_url


DURATION_RANGE_ERROR = (
    f'duration must be between 0 and {DURATION_S_MAX} seconds.'
)


def parse_duration(value: Any) -> int:
    """Parse and bound a client-supplied asset duration (seconds).

    Shared by the v1-family write paths, which model ``duration`` as a
    CharField for back-compat. Raises DRF ``ValidationError`` (plain
    message — callers decide the field key) so out-of-range values
    become a 400 instead of landing in the DB, where the viewer would
    feed them to ``threading.Event.wait`` and crash-loop on
    OverflowError (Sentry ANTHIAS-3E).
    """
    try:
        duration = int(value)
    except (TypeError, ValueError):
        raise ValidationError('A valid integer is required.')
    if not 0 <= duration <= DURATION_S_MAX:
        raise ValidationError(DURATION_RANGE_ERROR)
    return duration


def get_unique_name(name: str) -> str:
    names = Asset.objects.values_list('name', flat=True)
    if name in names:
        i = 1
        while True:
            new_name = f'{name}-{i}'
            if new_name in names:
                i += 1
            else:
                return new_name

    return name


def _is_within_assetdir(uri: str) -> bool:
    """True when ``uri`` resolves to a real path inside the asset dir.

    Absolute-path URIs are only legitimate for the two-step upload
    flow: the ``file_asset`` endpoint stages the upload at
    ``<assetdir>/<uuid>.tmp`` and the create request then submits that
    path. ``prepare_asset`` will ``rename`` a ``/``-prefixed URI into
    the asset store and the content endpoint reads it straight back, so
    without this guard a create request could point an asset at any
    host file the process can read (``/data/.anthias/anthias.conf`` —
    which holds ``django_secret_key`` — the SQLite DB, etc.), then GET
    its contents. ``realpath`` is applied to both sides so a symlink
    staged inside the asset dir can't resolve back out.
    """
    from anthias_server.settings import settings

    base = path.realpath(settings['assetdir']) + path.sep
    target = path.realpath(uri)
    return target.startswith(base)


def validate_uri(uri: str) -> None:
    # Raise DRF ``ValidationError`` (keyed on ``uri``) rather than a
    # bare ``Exception`` so ``serializer.is_valid()`` catches it and the
    # create view returns a 400 instead of a 500 — ``validate_uri`` only
    # runs inside the create serializers' ``prepare_asset``.
    if uri.startswith('/'):
        if not _is_within_assetdir(uri) or not path.isfile(uri):
            raise ValidationError(
                {'uri': 'Invalid file path. Failed to add asset.'}
            )
    else:
        if not validate_url(uri):
            raise ValidationError({'uri': 'Invalid URL. Failed to add asset.'})


class AssetSerializer(ModelSerializer[Asset]):
    duration = CharField()
    is_enabled = IntegerField(min_value=0, max_value=1)
    is_active = IntegerField(min_value=0, max_value=1)
    is_processing = IntegerField(min_value=0, max_value=1)
    nocache = IntegerField(min_value=0, max_value=1)
    skip_asset_check = IntegerField(min_value=0, max_value=1)
    # is_reachable is exposed as 0/1 (read-only) in v1 to match the
    # other boolean fields above; v2 returns it as a JSON bool.
    is_reachable = IntegerField(min_value=0, max_value=1, read_only=True)

    class Meta:
        model = Asset
        fields = [
            'asset_id',
            'name',
            'uri',
            'start_date',
            'end_date',
            'duration',
            'mimetype',
            'is_enabled',
            'nocache',
            'play_order',
            'skip_asset_check',
            'is_active',
            'is_processing',
            'is_reachable',
            'last_reachability_check',
            'metadata',
        ]
        read_only_fields = [
            'is_reachable',
            'last_reachability_check',
            # Owned by the upload-pipeline tasks; v1 exposes it read-only
            # for back-compat clients that want the original_ext /
            # transcoded / error bookkeeping. Mirrors v2.
            'metadata',
        ]


class UpdateAssetSerializer(Serializer[Asset]):
    # The fields below use `Field[Any, Any, Any, Any]` (instead of the
    # narrower IntegerField/CharField) so that v2's UpdateAssetSerializerV2
    # can override them with BooleanField/IntegerField. djangorestframework-
    # stubs treats Field subclasses as invariant on their type parameters,
    # so a narrower base type makes the override a [assignment] error. Do
    # NOT widen any other field "for consistency" — only widen those that
    # are actually overridden in subclasses.
    name = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration: Field[Any, Any, Any, Any] = CharField()
    is_enabled: Field[Any, Any, Any, Any] = IntegerField(
        min_value=0, max_value=1
    )
    is_processing: Field[Any, Any, Any, Any] = IntegerField(
        min_value=0, max_value=1, required=False
    )
    nocache: Field[Any, Any, Any, Any] = IntegerField(
        min_value=0, max_value=1, required=False
    )
    play_order = IntegerField(required=False)
    skip_asset_check: Field[Any, Any, Any, Any] = IntegerField(
        min_value=0, max_value=1, required=False
    )

    def validate_duration(self, value: Any) -> int:
        # Runs for v2's IntegerField override too — redundant there
        # (the field bounds already reject), but harmless.
        return parse_duration(value)

    def update(
        self,
        instance: Asset,
        validated_data: dict[str, Any],
    ) -> Asset:
        instance.name = validated_data.get('name', instance.name)
        instance.start_date = validated_data.get(
            'start_date', instance.start_date
        )
        instance.end_date = validated_data.get('end_date', instance.end_date)
        instance.is_enabled = validated_data.get(
            'is_enabled', instance.is_enabled
        )
        instance.is_processing = validated_data.get(
            'is_processing', instance.is_processing
        )
        instance.nocache = validated_data.get('nocache', instance.nocache)
        instance.play_order = validated_data.get(
            'play_order', instance.play_order
        )
        instance.skip_asset_check = validated_data.get(
            'skip_asset_check', instance.skip_asset_check
        )

        if 'video' not in (instance.mimetype or ''):
            instance.duration = validated_data.get(
                'duration', instance.duration
            )

        instance.save()

        return instance
