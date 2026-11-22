from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'anthias_server.api'
    label = 'api'

    def ready(self) -> None:
        from anthias_server.api import schema

        del schema
