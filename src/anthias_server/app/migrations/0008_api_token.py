import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anthias_app', '0007_asset_skip_ssl_verify'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ApiToken',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('name', models.CharField(max_length=100)),
                ('prefix', models.CharField(max_length=16)),
                (
                    'token_hash',
                    models.CharField(max_length=64, unique=True),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        default=django.utils.timezone.now
                    ),
                ),
                (
                    'last_used_at',
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    'revoked_at',
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='api_tokens',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'api_tokens',
                'ordering': ('-created_at', '-id'),
            },
        ),
    ]
