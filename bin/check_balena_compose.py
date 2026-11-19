#!/usr/bin/env python3
"""Refuse a balena deploy whose compose file balena would mangle.

`balena deploy` does not send our compose file to the fleet as written.
balena-cli runs it through balena-compose-parser, a Go binary, and the
release stores that parser's output. The two can disagree: the parser
drops every zero-valued ulimit field, so `core: 0` and
`core: {soft: 0, hard: 0}` both became `core: {}` in 2026.11.0. The
supervisor read that as an unset limit, never matched it against the
running container, and recreated the services forever. Every balena
device lost its web UI, and nothing failed at deploy time.

This runs the rendered file through the same parser binary that the
installed balena-cli will use, and fails if any service comes out with
a ulimit that is neither an integer nor a full {soft, hard} pair. That
is the shape the supervisor compares and Helios accepts.

Usage: check_balena_compose.py <rendered docker-compose.yml>

The parser is found inside the global npm install of balena-cli, which
is how CI installs it. Set BALENA_COMPOSE_PARSER to the binary's path
to override that, e.g. for a standalone balena-cli install.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

PARSER_IN_CLI = Path(
    'balena-cli',
    'node_modules',
    '@balena',
    'compose-parser',
    'bin',
    'balena-compose-parser',
)


def find_parser() -> Path:
    override = os.environ.get('BALENA_COMPOSE_PARSER')
    if override:
        return Path(override)
    npm_root = subprocess.run(
        ['npm', 'root', '-g'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(npm_root) / PARSER_IN_CLI


def parse(parser: Path, compose_file: str) -> dict[str, object]:
    # balena-cli runs the parser with an empty environment and a fresh
    # project name; mirror it so we see exactly what the deploy sends.
    result = subprocess.run(
        [str(parser), '-f', compose_file, str(uuid.uuid4())],
        check=True,
        capture_output=True,
        text=True,
        env={},
    )
    parsed: dict[str, object] = json.loads(result.stdout)
    return parsed


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def malformed_ulimits(composition: dict[str, object]) -> list[str]:
    """Every ulimit the supervisor could not compare, as messages."""
    problems: list[str] = []
    services = composition.get('services')
    if not isinstance(services, dict):
        return ['the parsed composition has no services mapping']
    for service_name, service in sorted(services.items()):
        limits = service.get('ulimits') if isinstance(service, dict) else None
        if not isinstance(limits, dict):
            continue
        for name, limit in sorted(limits.items()):
            if _is_int(limit):
                continue
            if (
                isinstance(limit, dict)
                and set(limit) == {'soft', 'hard'}
                and all(_is_int(v) for v in limit.values())
            ):
                continue
            problems.append(
                f'{service_name}: ulimits.{name} parsed as {json.dumps(limit)}'
            )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    compose_file = argv[1]

    parser = find_parser()
    if not parser.is_file():
        print(
            f'::error::balena-compose-parser not found at {parser}. '
            'Set BALENA_COMPOSE_PARSER to its path.',
            file=sys.stderr,
        )
        return 1

    problems = malformed_ulimits(parse(parser, compose_file))
    if problems:
        for problem in problems:
            print(
                f'::error title=balena would mangle the compose file::'
                f'{problem}. A zero-valued ulimit field is dropped by '
                'balena-compose-parser; see the ulimits comment in '
                'docker-compose.balena.yml.tmpl.',
                file=sys.stderr,
            )
        return 1

    print(f'{compose_file}: ulimits survive balena-compose-parser')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
