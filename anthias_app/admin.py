from django.contrib import admin

from anthias_app.models import (
    Asset,
    ScheduleSlot,
    ScheduleSlotItem,
)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        'asset_id',
        'name',
        'uri',
        'md5',
        'start_date',
        'end_date',
        'duration',
        'mimetype',
        'is_enabled',
        'is_processing',
        'is_active',
        'nocache',
        'play_order',
        'skip_asset_check',
    )


class ScheduleSlotItemInline(admin.TabularInline):
    model = ScheduleSlotItem
    extra = 0


@admin.register(ScheduleSlot)
class ScheduleSlotAdmin(admin.ModelAdmin):
    list_display = (
        'slot_id',
        'name',
        'slot_type',
        'time_from',
        'time_to',
        'days_of_week',
        'is_default',
        'sort_order',
    )
    inlines = [ScheduleSlotItemInline]


@admin.register(ScheduleSlotItem)
class ScheduleSlotItemAdmin(admin.ModelAdmin):
    list_display = (
        'item_id',
        'slot',
        'asset',
        'sort_order',
        'duration_override',
    )
