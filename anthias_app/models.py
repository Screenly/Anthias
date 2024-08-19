import uuid
from django.db import models # noqa F401


def generate_asset_id():
    return uuid.uuid4().hex


class Asset(models.Model):
    asset_id = models.TextField(
        primary_key=True, default=generate_asset_id, editable=False)
    name = models.TextField(blank=True, null=True)
    uri = models.TextField(blank=True, null=True)
    md5 = models.TextField(blank=True, null=True)
    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)
    duration = models.TextField(blank=True, null=True)
    mimetype = models.TextField(blank=True, null=True)
    is_enabled = models.IntegerField(default=0)
    is_processing = models.IntegerField(default=0)
    nocache = models.IntegerField(default=0)
    play_order = models.IntegerField(default=0)
    skip_asset_check = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'assets'
