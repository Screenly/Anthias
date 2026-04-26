# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anthias is an open-source digital signage platform for Raspberry Pi and x86 PCs (formerly Screenly OSE). It manages and displays media assets (images, videos, web pages) on connected screens.

## Architecture

Anthias runs as a set of Docker containers:

- **anthias-nginx** (port 80) — Reverse proxy, static file serving
- **anthias-server** (port 8000) — Django web app serving the React frontend and REST API
- **anthias-celery** — Async task queue (asset downloads, cleanup)
- **anthias-websocket** (port 9999) — Real-time updates
- **anthias-viewer** — Drives the display, receives instructions via ZMQ
- **redis** (port 6379) — Message broker, cache, database
- **webview** — Qt-based browser for rendering content on the display

Inter-service communication uses ZMQ (port 10001 publisher, 5558 collector). The primary database is SQLite stored at `~/.anthias/anthias.db`, with configuration in `~/.anthias/anthias.conf`. (Pre-rebrand installations have these at `~/.screenly/screenly.db` and `~/.screenly/screenly.conf`; `bin/migrate_legacy_paths.sh` migrates them on upgrade and leaves back-compat symlinks.)

### Key Directories

- `anthias_app/` — Django app (models, views, migrations, management commands)
- `anthias_django/` — Django project settings, URLs, ASGI/WSGI
- `api/` — REST API (views, serializers, URLs for v1, v1.1, v1.2, v2)
- `static/src/` — TypeScript/React frontend (components, Redux store, hooks, tests)
- `viewer/` — Viewer service (scheduling, media player, ZMQ communication)
- `webview/` — C++ Qt-based WebView (Qt5 for Pi 1-4, Qt6 for Pi 5/x86)
- `lib/` — Shared Python utilities (auth, device helpers, diagnostics, ZMQ)
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

```bash
# Build and start test containers
uv run python -m tools.image_builder --dockerfiles-only --disable-cache-mounts --service celery --service redis --service test
docker compose -f docker-compose.test.yml up -d --build

# Prepare and run tests (integration and non-integration must be run separately)
docker compose -f docker-compose.test.yml exec anthias-test bash ./bin/prepare_test_environment.sh -s
docker compose -f docker-compose.test.yml exec anthias-test ./manage.py test --exclude-tag=integration
docker compose -f docker-compose.test.yml exec anthias-test ./manage.py test --tag=integration
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
