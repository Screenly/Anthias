from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anthias_app', '0005_migrate_basic_auth_to_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
