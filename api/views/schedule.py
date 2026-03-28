import logging
from datetime import datetime, timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_app.models import Asset, ScheduleSlot, ScheduleSlotItem
from api.serializers.schedule import (
    CreateScheduleSlotItemSerializer,
    ReorderSlotItemsSerializer,
    ScheduleSlotItemSerializer,
    ScheduleSlotSerializer,
)
from lib.auth import authorized

logger = logging.getLogger(__name__)


def _recalculate_event_time_to(slot):
    """Recalculate time_to for event slots based on duration."""
    if slot.slot_type != 'event':
        return
    total_seconds = sum(
        item.effective_duration or 0
        for item in slot.items.select_related('asset').all()
    )
    base = datetime.combine(datetime.today(), slot.time_from)
    end = base + timedelta(seconds=total_seconds)
    slot.time_to = end.time()
    slot.save(update_fields=['time_to'])


class ScheduleSlotListView(APIView):
    """GET: list all schedule slots.  POST: create a new slot."""

    @extend_schema(
        summary='List schedule slots',
        responses={200: ScheduleSlotSerializer(many=True)},
    )
    @authorized
    def get(self, request):
        slots = ScheduleSlot.objects.all()
        serializer = ScheduleSlotSerializer(slots, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create schedule slot',
        request=ScheduleSlotSerializer,
        responses={201: ScheduleSlotSerializer},
    )
    @authorized
    def post(self, request):
        serializer = ScheduleSlotSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
        )


class ScheduleSlotDetailView(APIView):
    """GET / PUT / DELETE a single schedule slot."""

    def _get_slot(self, slot_id):
        try:
            return ScheduleSlot.objects.get(slot_id=slot_id)
        except ScheduleSlot.DoesNotExist:
            return None

    @extend_schema(
        summary='Get schedule slot',
        responses={200: ScheduleSlotSerializer},
    )
    @authorized
    def get(self, request, slot_id):
        slot = self._get_slot(slot_id)
        if slot is None:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ScheduleSlotSerializer(slot).data)

    @extend_schema(
        summary='Update schedule slot',
        request=ScheduleSlotSerializer,
        responses={200: ScheduleSlotSerializer},
    )
    @authorized
    def put(self, request, slot_id):
        slot = self._get_slot(slot_id)
        if slot is None:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = ScheduleSlotSerializer(
            slot,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )
        slot = serializer.save()
        _recalculate_event_time_to(slot)
        return Response(ScheduleSlotSerializer(slot).data)

    @extend_schema(
        summary='Partial update schedule slot',
        request=ScheduleSlotSerializer,
        responses={200: ScheduleSlotSerializer},
    )
    @authorized
    def patch(self, request, slot_id):
        return self.put(request, slot_id)

    @extend_schema(summary='Delete schedule slot')
    @authorized
    def delete(self, request, slot_id):
        slot = self._get_slot(slot_id)
        if slot is None:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        slot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScheduleSlotItemListView(APIView):
    """GET: list items in a slot.  POST: add an asset to a slot."""

    def _get_slot(self, slot_id):
        try:
            return ScheduleSlot.objects.get(slot_id=slot_id)
        except ScheduleSlot.DoesNotExist:
            return None

    @extend_schema(
        summary='List slot items',
        responses={
            200: ScheduleSlotItemSerializer(many=True),
        },
    )
    @authorized
    def get(self, request, slot_id):
        slot = self._get_slot(slot_id)
        if slot is None:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        items = slot.items.select_related('asset').all()
        return Response(
            ScheduleSlotItemSerializer(items, many=True).data,
        )

    @extend_schema(
        summary='Add asset to slot',
        request=CreateScheduleSlotItemSerializer,
        responses={201: ScheduleSlotItemSerializer},
    )
    @authorized
    def post(self, request, slot_id):
        slot = self._get_slot(slot_id)
        if slot is None:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CreateScheduleSlotItemSerializer(
            data=request.data,
        )
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        asset_id = serializer.validated_data['asset_id']
        try:
            asset = Asset.objects.get(asset_id=asset_id)
        except Asset.DoesNotExist:
            return Response(
                {'error': f'Asset {asset_id} not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if ScheduleSlotItem.objects.filter(
            slot=slot,
            asset=asset,
        ).exists():
            return Response(
                {
                    'error': ('This asset is already in this slot'),
                },
                status=status.HTTP_409_CONFLICT,
            )

        sort_order = serializer.validated_data.get(
            'sort_order',
            0,
        )
        if sort_order == 0:
            max_order = (
                slot.items.order_by('-sort_order')
                .values_list('sort_order', flat=True)
                .first()
            )
            sort_order = (max_order or 0) + 1

        item = ScheduleSlotItem.objects.create(
            slot=slot,
            asset=asset,
            sort_order=sort_order,
            duration_override=serializer.validated_data.get(
                'duration_override',
            ),
        )
        _recalculate_event_time_to(slot)
        return Response(
            ScheduleSlotItemSerializer(item).data,
            status=status.HTTP_201_CREATED,
        )


class ScheduleSlotItemDetailView(APIView):
    """PUT / DELETE a single item in a slot."""

    def _get_item(self, slot_id, item_id):
        try:
            return ScheduleSlotItem.objects.select_related(
                'asset',
            ).get(item_id=item_id, slot_id=slot_id)
        except ScheduleSlotItem.DoesNotExist:
            return None

    @extend_schema(
        summary='Update slot item',
        responses={200: ScheduleSlotItemSerializer},
    )
    @authorized
    def put(self, request, slot_id, item_id):
        item = self._get_item(slot_id, item_id)
        if item is None:
            return Response(
                {'error': 'Item not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if 'sort_order' in request.data:
            item.sort_order = int(request.data['sort_order'])
        if 'duration_override' in request.data:
            val = request.data['duration_override']
            item.duration_override = int(val) if val is not None else None

        item.save()
        _recalculate_event_time_to(item.slot)
        return Response(
            ScheduleSlotItemSerializer(item).data,
        )

    @extend_schema(
        summary='Partial update slot item',
        responses={200: ScheduleSlotItemSerializer},
    )
    @authorized
    def patch(self, request, slot_id, item_id):
        return self.put(request, slot_id, item_id)

    @extend_schema(summary='Delete slot item')
    @authorized
    def delete(self, request, slot_id, item_id):
        item = self._get_item(slot_id, item_id)
        if item is None:
            return Response(
                {'error': 'Item not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        slot = item.slot
        item.delete()
        _recalculate_event_time_to(slot)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScheduleSlotItemOrderView(APIView):
    """POST: reorder items within a slot."""

    @extend_schema(
        summary='Reorder slot items',
        request=ReorderSlotItemsSerializer,
        responses={
            200: ScheduleSlotItemSerializer(many=True),
        },
    )
    @authorized
    def post(self, request, slot_id):
        try:
            slot = ScheduleSlot.objects.get(slot_id=slot_id)
        except ScheduleSlot.DoesNotExist:
            return Response(
                {'error': 'Slot not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReorderSlotItemsSerializer(
            data=request.data,
        )
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        item_ids = serializer.validated_data['ids']
        for i, item_id in enumerate(item_ids):
            ScheduleSlotItem.objects.filter(
                item_id=item_id,
                slot=slot,
            ).update(sort_order=i)

        items = slot.items.select_related('asset').all()
        return Response(
            ScheduleSlotItemSerializer(items, many=True).data,
        )


class ScheduleStatusView(APIView):
    """GET: current schedule status."""

    @extend_schema(summary='Get schedule status')
    @authorized
    def get(self, request):
        slots = list(ScheduleSlot.objects.all())

        if not slots:
            return Response(
                {
                    'schedule_enabled': False,
                    'current_slot': None,
                    'next_change_at': None,
                    'total_slots': 0,
                    'using_default': False,
                }
            )

        active_event = None
        active_time = None
        default_slot = None
        for slot in slots:
            if slot.is_default:
                default_slot = slot
            elif slot.slot_type == 'event' and slot.is_currently_active():
                active_event = slot
            elif slot.is_currently_active():
                active_time = slot

        active_slot = active_event or active_time or None
        using_default = False
        if active_slot is None and default_slot is not None:
            active_slot = default_slot
            using_default = True

        next_change = _calc_next_change(active_slot, slots)

        return Response(
            {
                'schedule_enabled': True,
                'current_slot': (
                    ScheduleSlotSerializer(active_slot).data
                    if active_slot
                    else None
                ),
                'next_change_at': (
                    next_change.isoformat() if next_change else None
                ),
                'total_slots': len(slots),
                'using_default': using_default,
            }
        )


def _calc_next_change(active_slot, all_slots):
    """Calculate when the next slot transition occurs."""
    now = timezone.localtime()
    current_time = now.time()

    if active_slot is None:
        return _calc_next_slot_start(
            [s for s in all_slots if not s.is_default],
            now,
        )

    if active_slot.is_default:
        return _calc_next_slot_start(
            [s for s in all_slots if not s.is_default],
            now,
        )

    if active_slot.slot_type == 'event' or not active_slot.is_overnight:
        return now.replace(
            hour=active_slot.time_to.hour,
            minute=active_slot.time_to.minute,
            second=active_slot.time_to.second,
            microsecond=0,
        )

    if current_time >= active_slot.time_from:
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(
            hour=active_slot.time_to.hour,
            minute=active_slot.time_to.minute,
            second=0,
            microsecond=0,
        )
    else:
        return now.replace(
            hour=active_slot.time_to.hour,
            minute=active_slot.time_to.minute,
            second=0,
            microsecond=0,
        )


def _calc_next_slot_start(non_default_slots, now):
    """Find the nearest future moment when any slot starts."""
    candidates = []
    for slot in non_default_slots:
        days = slot.get_days_of_week()

        if not days and getattr(slot, 'start_date', None):
            candidate = now.replace(
                year=slot.start_date.year,
                month=slot.start_date.month,
                day=slot.start_date.day,
                hour=slot.time_from.hour,
                minute=slot.time_from.minute,
                second=0,
                microsecond=0,
            )
            if candidate > now:
                candidates.append(candidate)
            continue

        for day_offset in range(8):
            check_date = now + timedelta(days=day_offset)
            check_weekday = check_date.isoweekday()
            if days and check_weekday not in days:
                continue
            candidate = check_date.replace(
                hour=slot.time_from.hour,
                minute=slot.time_from.minute,
                second=0,
                microsecond=0,
            )
            if candidate > now:
                candidates.append(candidate)
                break

    return min(candidates) if candidates else None
