"""Manage the web UI's HTTPS certificate from the command line.

``bin/enable_ssl.sh`` calls this inside ``anthias-server`` so the CLI
and the Settings page end in exactly the same state:

    python -m anthias_server.manage web_tls status
    python -m anthias_server.manage web_tls custom --cert C.pem --key K.pem [--chain CHAIN.pem]
    python -m anthias_server.manage web_tls acme --domain example.com [--email me@x] [--staging]
    python -m anthias_server.manage web_tls default
    python -m anthias_server.manage web_tls apply
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from anthias_server.lib import caddy, node_identity, web_certificate


class Command(BaseCommand):
    help = "Manage the web UI's HTTPS certificate."

    def add_arguments(self, parser: CommandParser) -> None:
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status', help='Show the current certificate.')
        sub.add_parser('apply', help='Re-push the config to Caddy.')
        sub.add_parser('default', help='Serve the node certificate.')
        custom = sub.add_parser('custom', help='Install a certificate.')
        custom.add_argument('--cert', required=True, type=Path)
        custom.add_argument('--key', required=True, type=Path)
        custom.add_argument('--chain', type=Path)
        acme = sub.add_parser('acme', help="Use Let's Encrypt.")
        acme.add_argument('--domain', required=True)
        acme.add_argument('--email', default='')
        acme.add_argument('--staging', action='store_true')

    def handle(self, *args: Any, **options: Any) -> None:
        action = options['action']
        if action == 'status':
            self._status()
            return
        try:
            if action == 'apply':
                result = caddy.apply()
            elif action == 'default':
                result = caddy.apply_change(web_certificate.revert_to_default)
            elif action == 'custom':
                validated = web_certificate.validate(
                    self._read(options['cert']),
                    self._read(options['key']),
                    self._read(options['chain']) if options['chain'] else None,
                )
                result = caddy.apply_change(
                    lambda: web_certificate.install_custom(validated)
                )
            else:
                domain = web_certificate.validate_domain(options['domain'])
                email = web_certificate.validate_email(options['email'])
                result = caddy.apply_change(
                    lambda: web_certificate.set_acme(
                        domain, email, options['staging']
                    )
                )
        except web_certificate.CertificateError as exc:
            raise CommandError(str(exc)) from exc
        except caddy.CaddyConfigError as exc:
            raise CommandError(f'Caddy rejected the config: {exc}') from exc
        except caddy.CaddyError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Web certificate mode: {result.mode}')

    @staticmethod
    def _read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CommandError(f'Cannot read {path}: {exc}') from exc

    def _status(self) -> None:
        state = web_certificate.load_state()
        identity = node_identity.ensure_node_identity()
        self.stdout.write(f'Web certificate mode: {state.mode}')
        if state.mode == web_certificate.MODE_ACME:
            self.stdout.write(f'Domain: {state.domain}')
        info = (
            web_certificate.current_custom_info()
            if state.mode == web_certificate.MODE_CUSTOM
            else None
        )
        if info is not None:
            self.stdout.write(f'Subject: {info.subject}')
            self.stdout.write(f'Names: {", ".join(info.sans) or "-"}')
            self.stdout.write(f'Expires: {info.not_after:%Y-%m-%d}')
        self.stdout.write(f'Node fingerprint: {identity.fingerprint}')
