#!/usr/bin/env python
"""Screenshot the management UI without standing up the Docker stack.

Renders pages through Django's test client, serves the repo over a
throwaway HTTP server so ``{% static %}`` URLs resolve, and captures
each page with Playwright at several viewports and in both themes.

The Playwright integration suite is the real check, but it needs the
full compose stack. This is the fast loop for "did that CSS change
break the login page", which is otherwise invisible to the unit tests
because they only assert on markup.

    uv run python bin/shoot_pages.py --out /tmp/shots
    uv run python bin/shoot_pages.py --out /tmp/shots --only login
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC_ROOT = REPO / 'src/anthias_server/app'

os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE', 'anthias_server.django_project.settings'
)
# A scratch database per run. Without this the settings module resolves
# to the repo's shared test DB, and a capture run would mutate whatever
# the last pytest run left behind.
_SCRATCH_DB = Path(tempfile.gettempdir()) / 'anthias-shots.db'
os.environ.setdefault('ANTHIAS_TEST_DB_PATH', str(_SCRATCH_DB))

# Pages worth a look after any CSS change. The standalone roots come
# first: they have their own <head> and are the ones a change to the
# shared bundle is most likely to break unnoticed.
PAGES: dict[str, str] = {
    'login': '/login/',
    'splash': '/splash-page/',
    'home': '/',
    'settings': '/settings/',
    'system-info': '/system-info/',
    'integrations': '/integrations/',
    'design': '/_design/',
}

# Error pages are rendered from their template rather than fetched by
# URL. The suite runs with ENVIRONMENT=test, hence DEBUG=True, so
# requesting a missing path returns Django's own debug 404 and never
# reaches _error.html.
ERROR_TEMPLATES: dict[str, str] = {
    'error-404': '404.html',
    'error-500': '500.html',
    'error-403': '403.html',
}

VIEWPORTS = {
    'desktop': (1440, 900),
    'tablet': (900, 1200),
    'mobile': (390, 844),
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _serve(root: Path, port: int) -> ThreadingHTTPServer:
    """Serve `root` so /static/... resolves to the built bundles."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    httpd = ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _setup_django(seed: bool) -> None:
    """Boot Django with the test suite's Redis fake and a scratch DB.

    The Redis fake has to be installed before django.setup(), because
    several modules bind ``r = connect_to_redis()`` at import time.
    Reusing conftest's means this script needs no running broker.

    The scratch database is thrown away on every run, so this can never
    touch a real ~/.anthias/anthias.db.
    """
    sys.path.insert(0, str(REPO))
    import conftest

    conftest._patch_connect_to_redis()

    import django

    django.setup()

    from django.core.management import call_command

    call_command('migrate', run_syncdb=True, verbosity=0)

    if not seed:
        return

    # Same six-asset schedule the marketing captures use, so the table
    # renders every visual branch (mixed mimetypes, durations, and a
    # disabled row) rather than an empty state.
    from anthias_server.app.models import Asset
    from tests._seed_data import home_seed_assets

    if not Asset.objects.exists():
        for fields in home_seed_assets():
            Asset.objects.create(**fields)


def _render(paths: dict[str, str]) -> dict[str, str]:
    """Fetch each page's HTML through the Django test client."""
    from django.test import Client

    client = Client()
    html: dict[str, str] = {}
    for name, url in paths.items():
        response = client.get(url)
        body = response.content.decode('utf-8', 'replace')
        if not body.strip():
            print(f'  ! {name}: empty response ({response.status_code})')
            continue
        html[name] = body
        print(f'  . {name}: {response.status_code} ({len(body)} bytes)')
    return html


def _render_templates(names: dict[str, str]) -> dict[str, str]:
    """Render templates directly, bypassing URL resolution."""
    from django.template.loader import render_to_string

    html: dict[str, str] = {}
    for name, template in names.items():
        body = render_to_string(template)
        html[name] = body
        print(f'  . {name}: {template} ({len(body)} bytes)')
    return html


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument(
        '--only',
        nargs='*',
        help='page names to capture (default: all)',
    )
    parser.add_argument(
        '--themes',
        nargs='*',
        default=['light', 'dark'],
    )
    args = parser.parse_args()

    selected = set(args.only) if args.only else None
    wanted = {
        k: v for k, v in PAGES.items() if selected is None or k in selected
    }
    wanted_templates = {
        k: v
        for k, v in ERROR_TEMPLATES.items()
        if selected is None or k in selected
    }
    if not wanted and not wanted_templates:
        known = ', '.join([*PAGES, *ERROR_TEMPLATES])
        print(f'no such page; known: {known}')
        return 2

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    _setup_django(seed=True)

    print('rendering:')
    pages = _render(wanted) if wanted else {}
    if wanted_templates:
        pages.update(_render_templates(wanted_templates))
    if not pages:
        return 1

    # Django's test client emits absolute /static/... URLs, so the pages
    # are written into the tree the server roots at.
    staging = STATIC_ROOT / '_shots_tmp'
    staging.mkdir(exist_ok=True)
    try:
        for name, body in pages.items():
            (staging / f'{name}.html').write_text(body, encoding='utf-8')

        port = _free_port()
        httpd = _serve(STATIC_ROOT, port)
        try:
            from playwright.sync_api import sync_playwright

            print('capturing:')
            with sync_playwright() as p:
                browser = p.chromium.launch()
                for theme in args.themes:
                    for vp_name, (width, height) in VIEWPORTS.items():
                        context = browser.new_context(
                            viewport={'width': width, 'height': height},
                            device_scale_factor=2,
                        )
                        # Set before navigation so the boot script,
                        # once it exists, reads it on first paint.
                        context.add_init_script(
                            'try { localStorage.setItem('
                            f"'anthias.appearance', '{theme}'); }} "
                            'catch (e) {}'
                        )
                        page = context.new_page()
                        for name in pages:
                            page.goto(
                                f'http://127.0.0.1:{port}'
                                f'/_shots_tmp/{name}.html',
                                wait_until='networkidle',
                            )
                            page.evaluate(
                                't => document.documentElement'
                                '.setAttribute("data-theme", t)',
                                theme,
                            )
                            shot = out / f'{name}-{theme}-{vp_name}.png'
                            page.screenshot(path=str(shot), full_page=True)
                            print(f'  . {shot.name}')
                        context.close()
                browser.close()
        finally:
            httpd.shutdown()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f'\n{len(list(out.glob("*.png")))} screenshots in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
