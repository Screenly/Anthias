from django.contrib import admin

from anthias_app.models import Asset


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
