# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anthias is an open-source digital signage platform for Raspberry Pi and x86 PCs (formerly Screenly OSE). It manages and displays media assets (images, videos, web pages) on connected screens.

## Architecture

Anthias runs as a set of Docker containers:

- **anthias-server** (port 80 in prod, 8000 in dev) — uvicorn (ASGI) serving the Django web app, REST API, the React frontend's static assets (via WhiteNoise), uploaded media at `/anthias_assets/`, and the WebSocket endpoint at `/ws` (Django Channels with a Redis-backed channel layer). Always plain HTTP — TLS is opt-in and handled by the **anthias-caddy** sidecar that `bin/enable_ssl.sh` installs as a compose override (Caddy local CA by default, or auto Let's Encrypt with `--domain`, or BYO cert with `--cert`/`--key`).
- **anthias-celery** — Async task queue (asset downloads, cleanup). Runs the same image as `anthias-server` with a CMD override that starts the Celery worker; the two services share the entire root filesystem to avoid duplicating ~825 MB of identical apt content per device. Publishes asset-update events back to the WebSocket consumers via the Channels Redis layer.
- **anthias-viewer** — Drives the display, receives instructions over the Redis pub/sub `anthias.viewer` channel, talks to anthias-server over HTTP.
- **redis** (port 6379) — Celery broker + result backend, Channels channel layer, and the viewer signalling bus (pub/sub channel + per-correlation-ID reply lists).
- **webview** — Qt-based browser for rendering content on the display; fetches `/anthias_assets/` from anthias-server.

Inter-service messaging is all Redis: WebSocket fan-out from Celery to browsers goes via Channels/Redis, and server↔viewer commands/replies use Redis pub/sub on `anthias.viewer` with BLPOP on `anthias.reply.<correlation-id>` for the few request-reply paths. The primary database is SQLite stored at `~/.anthias/anthias.db`, with configuration in `~/.anthias/anthias.conf`. (Pre-rebrand installations have these at `~/.screenly/screenly.db` and `~/.screenly/screenly.conf`; `bin/migrate_legacy_paths.sh` migrates them on upgrade and leaves back-compat symlinks.)

### Key Directories

- `anthias_app/` — Django app (models, views, migrations, management commands)
- `anthias_django/` — Django project settings, URLs, ASGI/WSGI
- `api/` — REST API (views, serializers, URLs for v1, v1.1, v1.2, v2)
- `static/src/` — TypeScript/React frontend (components, Redux store, hooks, tests)
- `viewer/` — Viewer service (scheduling, media player, Redis pub/sub messaging)
- `webview/` — C++ Qt-based WebView (Qt5 for Pi 1-4, Qt6 for Pi 5/x86)
- `lib/` — Shared Python utilities (auth, device helpers, diagnostics)
- `docker/` — Dockerfile Jinja2 templates for each service
- `tests/` — Python unit/integration tests
- `bin/` — Shell scripts for install, dev setup, testing, upgrades
- `tools/` — Utilities including Docker image builder

## Development Commands

### Dev Environment

```bash
./bin/start_development_server.sh                    # Start full dev environment (Docker)
docker compose -f docker-compose.dev.yml down        # Stop dev server
# Web UI at http://localhost:8000
```

### Frontend (TypeScript/React)

```bash
bun install
bun run dev              # bun build + sass, both in watch mode
bun run build            # Production build
bun run lint:check       # ESLint check
bun run lint:fix         # ESLint fix
bun run format:check     # Prettier check
bun run format:fix       # Prettier fix
bun test                 # Run tests
```

Inside Docker:
```bash
docker compose -f docker-compose.dev.yml exec anthias-server bun run dev
```

### Python Linting

```bash
uv venv && uv pip install --group dev-host
uv run ruff check .                    # Lint all Python files
uv run ruff check /path/to/file.py     # Lint specific file
```

### Python Tests

#### Local development (no Docker, no Redis required)

The unit suite runs on the host via uv. The root `conftest.py` sets
`ENVIRONMENT=test`, force-mocks `lib.utils.connect_to_redis` for every
test, and stubs `gi`/`pydbus` so viewer modules import without the
distro PyGObject stack. The SQLite test DB lands at
`<repo>/.anthias-test.db` (gitignored); CI overrides via
`ANTHIAS_TEST_DB_PATH` in `docker-compose.test.yml`.

```bash
# One-time host prep: libcec headers (cec wheel build dep). Skip if
# the cec system package is already installed.
sudo apt-get install -y libcec-dev

uv sync --group test
uv run pytest -m "not integration"
```

Integration tests (`-m integration`) drive Selenium/Chrome and still
require the Docker stack; use the recipe below.

#### Docker-based runs (CI parity, integration suite)

```bash
# Build and start test containers
uv run python -m tools.image_builder --dockerfiles-only --disable-cache-mounts --service redis --service test
docker compose -f docker-compose.test.yml up -d --build

# Prepare and run tests (integration and non-integration must be run separately)
docker compose -f docker-compose.test.yml exec anthias-test bash ./bin/prepare_test_environment.sh -s
docker compose -f docker-compose.test.yml exec anthias-test pytest -n auto -m "not integration"
docker compose -f docker-compose.test.yml exec anthias-test pytest -m integration

# Coverage (CI uses these flags; --cov reads source/omit from pyproject.toml).
# CI fails the build when total line+branch coverage drops below 80%
# (`fail_under = 80` in [tool.coverage.report]).
docker compose -f docker-compose.test.yml exec anthias-test \
    pytest -n auto -m "not integration" --cov --cov-report=term
```

### Django Admin

```bash
export COMPOSE_FILE=docker-compose.dev.yml
docker compose exec anthias-server python manage.py createsuperuser
# Access at http://localhost:8000/admin/
```

## Coding Conventions

### Python
- Ruff for linting and formatting (line length: 79, single quotes)
- Target Python 3.11+
- Use type hints
- Exclude comments in generated code

### TypeScript/React
- Functional components only (no class components)
- Redux Toolkit (`createSlice`, `createAsyncThunk`) for state management
- No `any` or `unknown` types
- Don't explicitly import React (handled by `jsx: react-jsx` automatic runtime)
- Import order: built-in → third-party → local (alphabetically sorted, blank line between groups)
- Use `rem` instead of `px` in SCSS

### Qt/C++ (WebView)
- Use macros for Qt5/Qt6 cross-version compatibility

## API Versions

The REST API has multiple versions at `/api/v1/`, `/api/v1.1/`, `/api/v1.2/`, and `/api/v2/`. The v2 API (in `api/views/v2.py`) is the current primary API using DRF with drf-spectacular for OpenAPI schema generation.
