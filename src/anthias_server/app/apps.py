from django.apps import AppConfig


class AnthiasAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'anthias_server.app'
    label = 'anthias_app'

    def ready(self) -> None:
        # Playback envelope startup probe + re-render walker.
        # Cheap: one matrix lookup + one cache read. Walker only
        # fires if the cached envelope differs from the computed
        # one (board swap, Anthias upgrade landing a new matrix
        # value, hand-edited cache). Per-server-start side-effect
        # only — celery workers don't run this because the
        # `app` app isn't loaded in the worker process via the
        # ``ready`` hook (they import models directly and skip the
        # AppConfig.ready dance for the same-process registry).
        #
        # Deferred imports: ``AppConfig.ready`` runs before
        # ``django.db`` is fully primed for some apps (the worker's
        # autodiscover landed before this hook on older releases).
        # Importing inside the method keeps the import graph clean
        # — playback_envelope only depends on settings, which is
        # already loaded by the time AppConfig.ready fires.
        from anthias_server.app import startup

        startup.run_envelope_check()
