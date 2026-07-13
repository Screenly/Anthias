from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anthias_app', '0006_asset_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='skip_ssl_verify',
            field=models.BooleanField(default=False),
        ),
    ]
