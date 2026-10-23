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
        self._ensure_wal_journal_mode()

    def _ensure_wal_journal_mode(self) -> None:
        """Assert the database's WAL journal mode once per process.

        This app is in ``INSTALLED_APPS`` for every service that opens
        the shared SQLite file — uvicorn, the celery worker and the
        viewer — so whichever one starts into an uncontended moment
        performs the conversion and the rest find it already done. It
        deliberately does not use the ORM (``ready()`` must not), only a
        short-lived raw connection to the configured path, and it never
        raises: see ``lib.sqlite_journal`` for why the conversion cannot
        live on the per-connection path.

        Kept off ``ready()`` itself so its early return for a non-SQLite
        engine cannot skip the receiver registration above.
        """
        from django.conf import settings

        from anthias_server.lib.sqlite_journal import ensure_wal_journal_mode

        database = settings.DATABASES.get('default') or {}
        if 'sqlite3' not in database.get('ENGINE', ''):
            return
        ensure_wal_journal_mode(database.get('NAME'))
