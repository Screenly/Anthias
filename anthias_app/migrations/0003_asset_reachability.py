from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anthias_app', '0002_auto_20241015_1524'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='is_reachable',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='asset',
            name='last_reachability_check',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
