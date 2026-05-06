from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anthias_app', '0004_asset_schedule_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
