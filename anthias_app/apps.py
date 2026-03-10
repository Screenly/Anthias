import os

from django.apps import AppConfig


class AnthiasAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'anthias_app'

    def ready(self):
        if os.environ.get('RUN_MAIN') != 'true':
            return

        from anthias_app.tasks import start_background_scheduler

        start_background_scheduler()
