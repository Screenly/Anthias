"""Headless content import from a third-party signage provider.

Drives the same import-provider registry the wizard and API use, so a
support engineer can run a migration (or a dry-run manifest) over SSH
without the browser:

    manage.py import_content --provider yodeck --token '<label:secret>'
    manage.py import_content --provider yodeck --token '…' --dry-run
"""

from __future__ import annotations

from typing import Any

import requests
from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from anthias_server.lib.integrations.base import ProviderImportError
from anthias_server.lib.integrations.registry import get_provider


class Command(BaseCommand):
    help = 'Import media from a third-party signage provider.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--provider',
            required=True,
            help='Provider key (e.g. "yodeck").',
        )
        parser.add_argument(
            '--token',
            required=True,
            help="The provider's API token. Used only for this run.",
        )
        parser.add_argument(
            '--workspace',
            default=None,
            help='Optional provider workspace/account id to scope the import.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List the media that would be imported without importing.',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider = get_provider(options['provider'])
        if provider is None:
            raise CommandError(f'Unknown provider: {options["provider"]!r}')

        token = options['token']
        workspace = options['workspace']

        try:
            if not provider.validate_token(token):
                raise CommandError('The provider rejected this API token.')
            items = provider.list_media(token, workspace=workspace)
        except requests.RequestException as error:
            raise CommandError(f'Could not reach {provider.label}: {error}')

        importable = [item for item in items if item.importable]
        self.stdout.write(
            f'{len(items)} media found in {provider.label}; '
            f'{len(importable)} importable.'
        )

        if options['dry_run']:
            for item in items:
                mark = '✓' if item.importable else '–'
                reason = f'  ({item.skip_reason})' if item.skip_reason else ''
                self.stdout.write(
                    f'  [{mark}] {item.media_type:8} {item.name}{reason}'
                )
            return

        imported = skipped = failed = 0
        for item in importable:
            try:
                outcome = provider.import_item(token, item.remote_id)
            except ProviderImportError as error:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f'  ✗ {item.name}: {error.user_message}')
                )
                continue
            except requests.RequestException as error:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f'  ✗ {item.name}: {error}')
                )
                continue

            if outcome.skipped:
                skipped += 1
                self.stdout.write(f'  – {item.name}: {outcome.reason}')
            elif outcome.success:
                imported += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ {item.name}'))
            else:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  ✗ {item.name}: {outcome.reason or "failed"}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {imported} imported, {skipped} skipped, {failed} failed.'
            )
        )
