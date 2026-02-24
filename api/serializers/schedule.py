import json

from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework import serializers

from anthias_app.models import ScheduleSlot, ScheduleSlotItem


class ScheduleSlotItemSerializer(serializers.ModelSerializer):
    asset_name = serializers.CharField(
        source='asset.name',
        read_only=True,
    )
    asset_uri = serializers.CharField(
        source='asset.uri',
        read_only=True,
    )
    asset_mimetype = serializers.CharField(
        source='asset.mimetype',
        read_only=True,
    )
    asset_duration = serializers.IntegerField(
        source='asset.duration',
        read_only=True,
    )
    effective_duration = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleSlotItem
        fields = [
            'item_id',
            'slot_id',
            'asset_id',
            'sort_order',
            'duration_override',
            'asset_name',
            'asset_uri',
            'asset_mimetype',
            'asset_duration',
            'effective_duration',
        ]
        read_only_fields = [
            'item_id',
            'effective_duration',
        ]

    @extend_schema_field(OpenApiTypes.INT)
    def get_effective_duration(self, obj):
        if obj.duration_override is not None:
            return obj.duration_override
        return obj.asset.duration


class ScheduleSlotSerializer(serializers.ModelSerializer):
    items = ScheduleSlotItemSerializer(
        many=True,
        read_only=True,
    )
    is_currently_active = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleSlot
        fields = [
            'slot_id',
            'name',
            'slot_type',
            'time_from',
            'time_to',
            'days_of_week',
            'is_default',
            'start_date',
            'end_date',
            'no_loop',
            'sort_order',
            'items',
            'is_currently_active',
        ]
        read_only_fields = [
            'slot_id',
            'items',
            'is_currently_active',
        ]

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_currently_active(self, obj):
        return obj.is_currently_active()

    def to_internal_value(self, data):
        """Normalise days_of_week from list -> JSON string."""
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        dow = data.get('days_of_week')
        if isinstance(dow, list):
            data['days_of_week'] = json.dumps(dow)
        return super().to_internal_value(data)

    def to_representation(self, instance):
        """Deserialise days_of_week from JSON string -> list."""
        ret = super().to_representation(instance)
        raw = ret.get('days_of_week', '[]')
        if isinstance(raw, str):
            try:
                ret['days_of_week'] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                ret['days_of_week'] = [1, 2, 3, 4, 5, 6, 7]
        return ret

    def validate_days_of_week(self, value):
        """Ensure days_of_week is a valid JSON array of ints 1-7.

        Empty list is allowed for event slots (one-time events).
        """
        if isinstance(value, list):
            days = value
        else:
            try:
                days = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                raise serializers.ValidationError(
                    'days_of_week must be a JSON array of integers 1-7.'
                )

        if not isinstance(days, list):
            raise serializers.ValidationError('days_of_week must be a list.')
        for d in days:
            if not isinstance(d, int) or d < 1 or d > 7:
                raise serializers.ValidationError(
                    f'Invalid day: {d}. Must be 1 (Mon) - 7 (Sun).'
                )

        return json.dumps(sorted(set(days)))

    def validate_is_default(self, value):
        if not value:
            return value
        qs = ScheduleSlot.objects.filter(is_default=True)
        if self.instance:
            qs = qs.exclude(slot_id=self.instance.slot_id)
        if qs.exists():
            raise serializers.ValidationError(
                'A default slot already exists. Only one is allowed.'
            )
        return value

    def validate(self, attrs):
        slot_type = attrs.get(
            'slot_type',
            (self.instance.slot_type if self.instance else 'time'),
        )
        is_default = attrs.get(
            'is_default',
            (self.instance.is_default if self.instance else False),
        )

        if slot_type == 'event' and is_default:
            raise serializers.ValidationError(
                'Event slots cannot be marked as default.'
            )

        if slot_type == 'default':
            attrs['is_default'] = True
        elif slot_type == 'event':
            attrs['is_default'] = False
            attrs['no_loop'] = True

        if is_default or slot_type == 'default':
            return attrs

        time_from = attrs.get(
            'time_from',
            (self.instance.time_from if self.instance else None),
        )

        if slot_type == 'event':
            if time_from is None:
                raise serializers.ValidationError(
                    'time_from is required for event slots.'
                )
            if not self.instance:
                attrs['time_to'] = time_from
            return attrs

        time_to = attrs.get(
            'time_to',
            (self.instance.time_to if self.instance else None),
        )
        days_of_week_raw = attrs.get(
            'days_of_week',
            (
                self.instance.days_of_week
                if self.instance
                else '[1,2,3,4,5,6,7]'
            ),
        )

        if time_from is None or time_to is None:
            raise serializers.ValidationError(
                'time_from and time_to are required for non-default slots.'
            )

        if time_from == time_to:
            raise serializers.ValidationError(
                'time_from and time_to must be different.'
            )

        if isinstance(days_of_week_raw, str):
            new_days = set(json.loads(days_of_week_raw))
        else:
            new_days = set(days_of_week_raw)

        existing = ScheduleSlot.objects.filter(
            is_default=False,
            slot_type='time',
        )
        if self.instance:
            existing = existing.exclude(
                slot_id=self.instance.slot_id,
            )

        for slot in existing:
            slot_days = set(slot.get_days_of_week())
            common_days = new_days & slot_days
            if not common_days:
                continue
            if _time_ranges_overlap(
                (time_from, time_to),
                (slot.time_from, slot.time_to),
            ):
                raise serializers.ValidationError(
                    f'Time range overlaps with slot '
                    f'"{slot.name}" '
                    f'({slot.time_from}-{slot.time_to}) '
                    f'on shared days.'
                )

        return attrs


class CreateScheduleSlotItemSerializer(serializers.Serializer):
    """Serializer for adding an asset to a slot."""

    asset_id = serializers.CharField()
    sort_order = serializers.IntegerField(
        required=False,
        default=0,
    )
    duration_override = serializers.IntegerField(
        required=False,
        default=None,
        allow_null=True,
    )


class ReorderSlotItemsSerializer(serializers.Serializer):
    """Serializer for reordering items within a slot."""

    ids = serializers.ListField(
        child=serializers.CharField(),
    )


def _time_ranges_overlap(range_a, range_b):
    """Check if two (time_from, time_to) intervals overlap.

    Handles overnight slots where time_from > time_to.
    Converts to minute-based intervals within a 0-1440 range,
    splitting overnight ranges into two segments.
    """

    def expand(tf, tt):
        sf = tf.hour * 60 + tf.minute
        st = tt.hour * 60 + tt.minute
        if sf < st:
            return [(sf, st)]
        else:
            return [(sf, 1440), (0, st)]

    for a_start, a_end in expand(*range_a):
        for b_start, b_end in expand(*range_b):
            if a_start < b_end and b_start < a_end:
                return True
    return False
