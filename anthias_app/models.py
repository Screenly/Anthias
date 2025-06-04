import uuid

from django.db import models
from django.utils import timezone


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
    duration = models.BigIntegerField(blank=True, null=True)
    mimetype = models.TextField(blank=True, null=True)
    is_enabled = models.BooleanField(default=False)
    is_processing = models.BooleanField(default=False)
    nocache = models.BooleanField(default=False)
    play_order = models.IntegerField(default=0)
    skip_asset_check = models.BooleanField(default=False)

    class Meta:
        db_table = 'assets'

    def __str__(self):
        return self.name

    def is_active(self):
        if self.is_enabled and self.start_date and self.end_date:
            current_time = timezone.now()
            return (
                self.start_date < current_time < self.end_date
            )

        return False
