from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from anthias_app.models import Asset


class Command(BaseCommand):
    help = 'Seeds the database with sample web assets'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing assets before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing assets...')
            Asset.objects.all().delete()

        self.stdout.write('Creating sample web assets...')

        # Create some sample web assets
        assets = [
            {
                'name': 'Google Homepage',
                'uri': 'https://www.google.com',
                'mimetype': 'text/html',
                'is_enabled': False,
                'start_date': timezone.now(),
                'end_date': timezone.now() + timedelta(days=7),
                'play_order': 0,
            },
            {
                'name': 'GitHub Homepage',
                'uri': 'https://github.com',
                'mimetype': 'text/html',
                'is_enabled': False,
                'start_date': timezone.now(),
                'end_date': timezone.now() + timedelta(days=14),
                'play_order': 0,
            },
            {
                'name': 'Django Documentation',
                'uri': 'https://docs.djangoproject.com',
                'mimetype': 'text/html',
                'is_enabled': False,
                'start_date': timezone.now() - timedelta(days=1),
                'end_date': timezone.now() + timedelta(days=21),
                'play_order': 0,
            },
            {
                'name': 'Vue.js Homepage',
                'uri': 'https://vuejs.org',
                'mimetype': 'text/html',
                'is_enabled': False,
                'start_date': timezone.now() - timedelta(days=1),
                'end_date': timezone.now() + timedelta(days=21),
                'play_order': 0,
            }
        ]

        for asset_data in assets:
            Asset.objects.create(**asset_data)
            self.stdout.write(
                self.style.SUCCESS(f'Created web asset: {asset_data["name"]}')
            )

        self.stdout.write(self.style.SUCCESS('Successfully seeded the database with web assets'))
