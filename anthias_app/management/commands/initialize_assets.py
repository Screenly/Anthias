from os import mkdir, makedirs, path
from django.core.management.base import BaseCommand, CommandError
from lib import assets_helper, db, queries
from settings import settings


# This function is used to be called when the first request is made to the server,
# back when the server's written in Flask.
def initialize_assets_directories():
    # Make sure the asset folder exist. If not, create it.
    if not path.isdir(settings['assetdir']):
        mkdir(settings['assetdir'])

    # Create config dir if it doesn't exist.
    if not path.isdir(settings.get_configdir()):
        makedirs(settings.get_configdir())

    with db.conn(settings['database']) as conn:
        with db.cursor(conn) as cursor:
            cursor.execute(queries.exists_table)
            if cursor.fetchone() is None:
                cursor.execute(assets_helper.create_assets_table)

class Command(BaseCommand):
    help = (
        "Ensures that the config & assets folders exist and that the "
        "database is initialized"
    )

    def handle(self, *args, **options):
        try:
            initialize_assets_directories()
        except Exception as error:
            raise CommandError(str(error))

        self.stdout.write(self.style.SUCCESS('Assets are initialized successfully.'))
