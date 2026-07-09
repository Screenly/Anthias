from datetime import timezone
from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.serializers import (
    BooleanField,
    CharField,
    ChoiceField,
    DateTimeField,
    DictField,
    IntegerField,
    ListField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
    TimeField,
)

from anthias_common.utils import SCREEN_ROTATION_CHOICES
from anthias_server.django_project.settings import is_valid_time_zone
from anthias_server.app.models import (
    Asset,
    DURATION_S_MAX,
    MAX_ASSET_HEADERS,
    REFRESH_INTERVAL_S_MAX,
    clamp_refresh_interval,
    normalize_asset_headers,
    validate_asset_headers,
)
from anthias_server.api.serializers import UpdateAssetSerializer
from anthias_server.api.serializers.mixins import CreateAssetSerializerMixin


def _normalise_play_days(value: list[int]) -> list[int]:
    """Return a sorted, deduped list of weekday ints. Raises
    ValidationError for items outside [1..7] or for an empty selection.

    Empty `play_days` is rejected explicitly: silently widening it to
    "all days" would surprise an operator who unchecked everything
    expecting "never play". Disabling the asset (is_enabled=false) is
    the right primitive for that intent.

    Stays a list so DRF's ListField.to_representation can round-trip
    through serializer.data (the create view passes that dict straight
    into Asset.objects.create()). The TextField column stringifies the
    list at save time.
    """
    for d in value:
        if not isinstance(d, int) or d < 1 or d > 7:
            raise serializers.ValidationError(
                f'Invalid day: {d}. Must be 1 (Mon) - 7 (Sun).'
            )
    deduped = sorted(set(value))
    if not deduped:
        raise serializers.ValidationError(
            'play_days must contain at least one day. To stop playback '
            'entirely, disable the asset (is_enabled=false).'
        )
    return deduped


# Per-asset webpage auto-refresh cadence is stored inside ``Asset.metadata``
# but exposed as a top-level field on the v2 serializers so ``metadata``
# can stay read-only (the upload pipeline owns those keys). The cap
# itself lives on the model (REFRESH_INTERVAL_S_MAX, imported above) so
# the form handler in app/views.py and the v2 API agree on the same
# value without drift.


def _validate_custom_headers(value: Any) -> dict[str, str]:
    """Strictly validate the write-side ``custom_headers`` payload,
    translating the model's ``ValueError`` into a DRF 400.

    Delegates to ``validate_asset_headers`` (the single source of truth
    for the header-name / value rules and the count cap) so the API and
    the viewer read path can't drift apart.

    On failure we raise a fixed, self-authored message rather than
    surfacing the caught exception's text: the rules are few and the
    operator supplied the offending value themselves, so a static
    description is just as actionable and keeps any exception detail off
    the HTTP response (CodeQL "information exposure through an
    exception").
    """
    try:
        return validate_asset_headers(value)
    except ValueError:
        raise serializers.ValidationError(
            'Invalid custom headers. Each entry needs a valid header-name '
            "token (letters, digits, and !#$%&'*+-.^_`|~) and a value with "
            f'no CR/LF, and at most {MAX_ASSET_HEADERS} headers are allowed.'
        ) from None


def _validate_time_window(
    attrs: dict[str, Any],
    instance: Asset | None = None,
) -> dict[str, Any]:
    """Both play_time_from and play_time_to must be set, or neither.

    The model treats either side being null as "no time-of-day filter",
    so a partial window would silently disable the constraint while
    the UI showed it as enabled. We check the *post-update* state so
    PATCHes that touch only one field still see the merged result.
    """

    def resolve(field: str) -> Any:
        if field in attrs:
            return attrs[field]
        if instance is not None:
            return getattr(instance, field, None)
        return None

    has_from = resolve('play_time_from') is not None
    has_to = resolve('play_time_to') is not None
    if has_from != has_to:
        raise serializers.ValidationError(
            {
                'play_time_to' if has_from else 'play_time_from': (
                    'play_time_from and play_time_to must be set together.'
                )
            }
        )
    return attrs


class AssetSerializerV2(ModelSerializer[Asset], CreateAssetSerializerMixin):
    is_active = SerializerMethodField()
    play_days = SerializerMethodField()
    refresh_interval_s = SerializerMethodField()
    custom_headers = SerializerMethodField()
    metadata = SerializerMethodField()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_active(self, obj: Asset) -> bool:
        # When the caller has already evaluated activeness against a
        # shared ``now`` (e.g. ViewerPlaylistViewV2 freezes it once so
        # the filter and the deadline computation can't disagree
        # across a window-boundary tick), accept the same timestamp
        # via context so this field renders against the same instant
        # rather than re-reading ``timezone.now()`` a few ms later.
        # Default path (no context['now']) preserves the previous
        # "evaluate at render time" behaviour for every other caller.
        now = self.context.get('now')
        return obj.is_active(now=now)

    @extend_schema_field({'type': 'array', 'items': {'type': 'integer'}})
    def get_play_days(self, obj: Asset) -> list[int]:
        return obj.get_play_days()

    @extend_schema_field(OpenApiTypes.INT)
    def get_refresh_interval_s(self, obj: Asset) -> int:
        # Pulled out of metadata so it shows up as a first-class column
        # on GET; the field is itself written from UpdateAssetSerializerV2
        # back into metadata. Default 0 = no auto-refresh, mirroring the
        # viewer's handling for assets without the key set.
        return clamp_refresh_interval(
            (obj.metadata or {}).get('refresh_interval_s', 0)
        )

    @extend_schema_field(
        {'type': 'object', 'additionalProperties': {'type': 'string'}}
    )
    def get_custom_headers(self, obj: Asset) -> dict[str, str]:
        # Per-asset custom request headers for webpage assets (#2215),
        # surfaced as a first-class field like ``refresh_interval_s`` but
        # written back into ``metadata['headers']``. Sanitised on read so
        # a legacy / hand-edited row can never echo an unsafe (CR/LF)
        # value or a non-string blob. Empty {} = no custom headers.
        return normalize_asset_headers((obj.metadata or {}).get('headers'))

    @extend_schema_field({'type': 'object', 'additionalProperties': True})
    def get_metadata(self, obj: Asset) -> dict[str, Any]:
        # Sanitise ``refresh_interval_s`` in the embedded metadata too,
        # so a legacy/hand-edited row can't return a top-level
        # ``refresh_interval_s: 0`` while the ``metadata`` field still
        # echoes the raw out-of-range value. Other keys (the upload-
        # pipeline's original_ext / transcoded / error_message) pass
        # through untouched.
        raw = dict(obj.metadata or {})
        if 'refresh_interval_s' in raw:
            raw['refresh_interval_s'] = clamp_refresh_interval(
                raw['refresh_interval_s']
            )
        # Same posture for the custom headers bag: keep the embedded
        # ``metadata.headers`` consistent with the top-level
        # ``custom_headers`` field a client also reads off this response.
        if 'headers' in raw:
            raw['headers'] = normalize_asset_headers(raw['headers'])
        return raw

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
            'play_days',
            'play_time_from',
            'play_time_to',
            'is_reachable',
            'last_reachability_check',
            'metadata',
            'refresh_interval_s',
            'custom_headers',
        ]
        read_only_fields = [
            'is_reachable',
            'last_reachability_check',
            # ``metadata`` is owned by the upload-pipeline tasks
            # (image_normalize_asset, video_normalize_asset). Operators
            # can read the original-extension / transcoded / error
            # bookkeeping but can't overwrite it from the API — letting
            # them stomp on it would invite "transcoded=true but the
            # file is the original" desync. Same posture as
            # is_reachable / last_reachability_check above. The webpage
            # auto-refresh interval is surfaced as its own writable
            # field (refresh_interval_s) so operators can edit just
            # that one key without opening the whole bag.
            'metadata',
        ]


class CreateAssetSerializerV2(
    Serializer[dict[str, Any]], CreateAssetSerializerMixin
):
    def __init__(
        self,
        *args: Any,
        unique_name: bool = False,
        **kwargs: Any,
    ) -> None:
        self.unique_name = unique_name
        super().__init__(*args, **kwargs)

    asset_id = CharField(read_only=True)
    ext = CharField(write_only=True, required=False)
    name = CharField()
    uri = CharField()
    start_date = DateTimeField(default_timezone=timezone.utc)
    end_date = DateTimeField(default_timezone=timezone.utc)
    duration = IntegerField(min_value=0, max_value=DURATION_S_MAX)
    mimetype = CharField()
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    play_order = IntegerField(required=False)
    skip_asset_check = BooleanField(required=False)
    play_days = ListField(
        child=IntegerField(min_value=1, max_value=7),
        required=False,
    )
    play_time_from = TimeField(required=False, allow_null=True)
    play_time_to = TimeField(required=False, allow_null=True)
    # write_only because ``Asset`` has no ``refresh_interval_s`` column
    # — the value lives inside ``metadata``. Keeping it out of
    # ``serializer.data`` avoids ``Asset.objects.create(**serializer.data)``
    # crashing on an unknown kwarg in the v2 POST view; the field is
    # surfaced back on the response via ``AssetSerializerV2``'s
    # SerializerMethodField. The view applies ``validated_data['metadata']``
    # (set in ``validate()`` below) to the persisted row after create().
    refresh_interval_s = IntegerField(
        required=False,
        write_only=True,
        min_value=0,
        max_value=REFRESH_INTERVAL_S_MAX,
    )
    # write_only for the same reason as ``refresh_interval_s`` above:
    # ``Asset`` has no ``custom_headers`` column — the value lives inside
    # ``metadata['headers']`` and is surfaced back via
    # ``AssetSerializerV2.get_custom_headers``. ``DictField`` gives a
    # clean 400 on a non-object; ``validate_custom_headers`` enforces the
    # header-name/value rules. No ``child=CharField``: that would *coerce*
    # a numeric/boolean JSON value (``{"X": 123}`` -> ``"123"``) instead
    # of rejecting it. The unvalidated child passes values through
    # verbatim so ``validate_asset_headers``' ``isinstance(str)`` gate
    # can 400 non-string values (and no CharField trimming mangles a
    # value we send on the wire byte-for-byte).
    custom_headers = DictField(required=False, write_only=True)

    def validate_play_days(self, value: Any) -> list[int]:
        return _normalise_play_days(value)

    def validate_custom_headers(self, value: Any) -> dict[str, str]:
        return _validate_custom_headers(value)

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        _validate_time_window(data)
        prepared = self.prepare_asset(data, version='v2')
        # POST round-trip for the webpage auto-refresh interval. Land it
        # in ``metadata`` so a fresh row that gets created with a
        # refresh interval doesn't need a follow-up PATCH to take
        # effect. Skipping the key entirely (rather than storing 0)
        # keeps ``metadata`` a clean ``{}`` for assets that didn't ask
        # for auto-refresh, matching what the upload pipeline expects.
        # ``metadata`` is not a declared field on this serializer, so
        # it appears in ``validated_data`` but not in ``serializer.data``
        # — the v2 POST view reads it from ``validated_data`` and
        # applies it to the asset after Asset.objects.create().
        if 'refresh_interval_s' in data:
            metadata = dict(prepared.get('metadata') or {})
            metadata['refresh_interval_s'] = int(data['refresh_interval_s'])
            prepared['metadata'] = metadata
        # POST round-trip for the per-asset custom headers, mirroring the
        # refresh-interval handling above: fold into ``metadata`` so a
        # freshly-created webpage asset carries its headers without a
        # follow-up PATCH. An explicit empty object clears the key rather
        # than storing ``{}`` so ``metadata`` stays clean for assets that
        # didn't ask for headers.
        if 'custom_headers' in data:
            metadata = dict(prepared.get('metadata') or {})
            headers = data['custom_headers']
            if headers:
                metadata['headers'] = headers
            else:
                metadata.pop('headers', None)
            prepared['metadata'] = metadata
        return prepared


class UpdateAssetSerializerV2(UpdateAssetSerializer):
    is_enabled = BooleanField()
    is_processing = BooleanField(required=False)
    nocache = BooleanField(required=False)
    skip_asset_check = BooleanField(required=False)
    duration = IntegerField(min_value=0, max_value=DURATION_S_MAX)
    play_days = ListField(
        child=IntegerField(min_value=1, max_value=7),
        required=False,
    )
    play_time_from = TimeField(required=False, allow_null=True)
    play_time_to = TimeField(required=False, allow_null=True)
    refresh_interval_s = IntegerField(
        required=False,
        min_value=0,
        max_value=REFRESH_INTERVAL_S_MAX,
    )
    # No ``child=CharField`` — see CreateAssetSerializerV2.custom_headers:
    # the unvalidated child preserves value types so a non-string value is
    # rejected (not coerced) and header values aren't trimmed.
    custom_headers = DictField(required=False)

    def validate_play_days(self, value: Any) -> list[int]:
        return _normalise_play_days(value)

    def validate_custom_headers(self, value: Any) -> dict[str, str]:
        return _validate_custom_headers(value)

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return _validate_time_window(data, instance=self.instance)

    def update(self, instance: Asset, validated_data: dict[str, Any]) -> Asset:
        # Apply schedule fields before delegating: super().update() calls
        # instance.save() at the end, so this lands in a single write.
        for field in ('play_days', 'play_time_from', 'play_time_to'):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        if 'refresh_interval_s' in validated_data:
            # Merge into metadata so pipeline-owned keys (original_ext,
            # transcoded, error_message) survive the update — clobbering
            # them via dict assignment would resurrect the
            # "transcoded=true but file is original" desync we made
            # ``metadata`` read-only to prevent. For non-webpage assets
            # we accept and persist the value but it's a no-op at
            # playback time (viewer only branches on refresh_interval_s
            # for ``mimetype contains 'web'``).
            metadata = dict(instance.metadata or {})
            metadata['refresh_interval_s'] = int(
                validated_data['refresh_interval_s']
            )
            instance.metadata = metadata
        if 'custom_headers' in validated_data:
            # Merge into metadata for the same reason as
            # refresh_interval_s: pipeline-owned keys (original_ext,
            # transcoded, error_message) must survive. An empty object
            # clears the headers rather than storing ``{}``. Like
            # refresh_interval_s, we accept and persist this on any
            # asset, but the viewer only injects it for webpage assets.
            metadata = dict(instance.metadata or {})
            headers = validated_data['custom_headers']
            if headers:
                metadata['headers'] = headers
            else:
                metadata.pop('headers', None)
            instance.metadata = metadata
        return super().update(instance, validated_data)


class DeviceSettingsSerializerV2(Serializer[Any]):
    player_name = CharField()
    audio_output = CharField()
    default_duration = IntegerField()
    default_streaming_duration = IntegerField()
    date_format = CharField()
    timezone = CharField(allow_blank=True)
    auth_backend = CharField()
    show_splash = BooleanField()
    default_assets = BooleanField()
    shuffle_playlist = BooleanField()
    use_24_hour_clock = BooleanField()
    debug_logging = BooleanField()
    prefer_dark_mode = BooleanField()
    # Mirror the PATCH-side ChoiceField so the OpenAPI schema
    # advertises the same enum on both directions — clients can rely
    # on the value being one of {0, 90, 180, 270} when reading too.
    screen_rotation = ChoiceField(choices=SCREEN_ROTATION_CHOICES)
    username = CharField()


class UpdateDeviceSettingsSerializerV2(Serializer[Any]):
    player_name = CharField(required=False, allow_blank=True)
    audio_output = CharField(required=False)
    # Bounded like Asset.duration — these defaults get copied onto new
    # asset rows (CreateAssetSerializerMixin, HTML add-asset path), so
    # a poisoned default would reach the viewer's Event.wait the same
    # way a bad per-asset duration does (Sentry ANTHIAS-3E).
    default_duration = IntegerField(
        required=False, min_value=0, max_value=DURATION_S_MAX
    )
    default_streaming_duration = IntegerField(
        required=False, min_value=0, max_value=DURATION_S_MAX
    )
    date_format = CharField(required=False)
    # Blank defers to resolve_time_zone() (TZ env -> /etc/timezone ->
    # UTC). A non-blank value must be a zone Django will accept,
    # validated the same way as the host zone so a save can never land a
    # value that crash-loops the settings module on the next read.
    timezone = CharField(required=False, allow_blank=True)
    show_splash = BooleanField(required=False)
    default_assets = BooleanField(required=False)
    shuffle_playlist = BooleanField(required=False)
    use_24_hour_clock = BooleanField(required=False)
    debug_logging = BooleanField(required=False)
    prefer_dark_mode = BooleanField(required=False)
    screen_rotation = ChoiceField(
        required=False, choices=SCREEN_ROTATION_CHOICES
    )
    username = CharField(required=False, allow_blank=True)
    password = CharField(required=False, allow_blank=True)
    password_2 = CharField(required=False, allow_blank=True)
    auth_backend = ChoiceField(
        required=False,
        allow_blank=True,
        choices=[
            ('', 'No authentication'),
            ('auth_basic', 'Basic authentication'),
        ],
    )
    current_password = CharField(required=False, allow_blank=True)

    def validate_timezone(self, value: str) -> str:
        value = (value or '').strip()
        if value and not is_valid_time_zone(value):
            raise serializers.ValidationError(
                f'Unknown or unavailable timezone: {value}.'
            )
        return value


class ViewerPlaylistSerializerV2(Serializer[Any]):
    """Server-evaluated playlist for the C++ viewer.

    The deadline is the soonest UTC moment at which the viewer should
    re-fetch this endpoint — derived from asset start/end boundaries
    plus a 60s cap when any returned asset has a day-of-week or
    time-of-day window. ``now`` is the server's notion of the
    evaluation timestamp; clients use it to compute deadline-relative
    sleeps without trusting their own clock.
    """

    assets = AssetSerializerV2(many=True)
    deadline = DateTimeField(allow_null=True)
    now = DateTimeField()


class ViewerSettingsSerializerV2(Serializer[Any]):
    """Viewer-relevant subset of device settings.

    Intentionally narrower than ``DeviceSettingsSerializerV2``: only
    the keys the viewer actually consults at runtime are exposed, so
    the C++ viewer's internal-auth path doesn't pull operator fields
    (``username``, ``auth_backend``, ``player_name``, …) it never
    needs to read.
    """

    shuffle_playlist = BooleanField()
    show_splash = BooleanField()
    screen_rotation = ChoiceField(choices=SCREEN_ROTATION_CHOICES)
    audio_output = CharField()
    debug_logging = BooleanField()


class IntegrationsSerializerV2(Serializer[Any]):
    is_balena = BooleanField()
    balena_device_id = CharField(required=False, allow_null=True)
    balena_app_id = CharField(required=False, allow_null=True)
    balena_app_name = CharField(required=False, allow_null=True)
    balena_supervisor_version = CharField(required=False, allow_null=True)
    balena_host_os_version = CharField(required=False, allow_null=True)
    balena_device_name_at_init = CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )


class ScreenlyTokenSerializerV2(Serializer[Any]):
    token = CharField(write_only=True)


class ScreenlyMigrateAssetSerializerV2(Serializer[Any]):
    token = CharField(write_only=True)
    asset_id = CharField()
    asset_group_id = CharField(required=False, allow_blank=True)


class ImportValidateSerializerV2(Serializer[Any]):
    """Request body for validating an import provider's token."""

    token = CharField(write_only=True)


class ImportItemSerializerV2(Serializer[Any]):
    """Request body for importing a single remote media item.

    ``enable`` defaults to True so the wizard's per-item calls don't have
    to send it on every request; the operator toggles it once and it
    rides on each item POST.
    """

    token = CharField(write_only=True)
    remote_id = CharField()
    enable = BooleanField(required=False, default=True)
