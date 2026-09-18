from django.apps import AppConfig


class AnthiasAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'anthias_server.app'
    label = 'anthias_app'

    def ready(self) -> None:
        # Connects the User post_save/post_delete receiver that revokes
        # open /ws sockets on a credential change. Imported here rather
        # than at module scope because it touches the auth models, which
        # aren't loaded until the app registry is populated.
        from anthias_server.app.signals import register

        register()
