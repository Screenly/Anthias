# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anthias is an open-source digital signage platform for Raspberry Pi and x86 PCs (formerly Screenly OSE). It manages and displays media assets (images, videos, web pages) on connected screens.

## Architecture

Anthias runs as 2 Docker containers:

- **anthias-server** (port 8000) — Django ASGI app (Daphne) serving the HTMX UI, REST API, WebSocket endpoints, and static files (WhiteNoise)
- **anthias-viewer** — Drives the display via Qt WebView, receives commands via WebSocket

Inter-service communication uses Django Channels WebSocket (`/ws/viewer/` for viewer commands, `/ws/ui/` for UI updates). The primary database is SQLite stored at `~/.screenly/screenly.db`, with configuration in `~/.screenly/screenly.conf`.

### Key Directories

- `anthias_app/` — Django app (models, views, templates, migrations, tasks, consumers)
- `anthias_django/` — Django project settings, URLs, ASGI routing
- `api/` — REST API v2 (views, serializers, URLs)
- `viewer/` — Viewer service (scheduling, media player, WebSocket client)
- `webview/` — C++ Qt-based WebView (Qt5 for Pi 1-4, Qt6 for Pi 5/x86)
- `lib/` — Shared Python utilities (auth, device helpers, diagnostics)
- `docker/` — Dockerfile Jinja2 templates for each service
- `tests/` — Python unit/integration tests
- `bin/` — Shell scripts for install, dev setup, testing, upgrades
- `tools/` — Utilities including Docker image builder

## Development Commands

### Dev Environment

```bash
docker compose -f docker-compose.dev.yml up --build    # Start dev environment
docker compose -f docker-compose.dev.yml down           # Stop dev server
# Web UI at http://localhost:8000
```

### Python Dependencies

All Python dependencies are managed via `pyproject.toml` dependency groups, with `uv.lock` for reproducible installs:

- `server` — Django web app (anthias-server)
- `viewer` — Viewer service
- `wifi-connect` — WiFi Connect service
- `dev` — Test utilities (mock, selenium, etc.)
- `test` — Combined group (includes server + dev + viewer)
- `host` — Host machine (Ansible, etc.)
- `local` — Local CLI tools
- `dev-host` — Ruff linter
- `docker-image-builder` — Docker image build tooling

```bash
uv lock                                # Regenerate lockfile after changing deps
uv sync --only-group <name>            # Install a specific group
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
uv run python -m tools.image_builder --dockerfiles-only --disable-cache-mounts --service test
docker compose -f docker-compose.test.yml up -d --build

# Prepare and run tests
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

### Frontend (Django Templates + HTMX)
- Django templates with HTMX for dynamic interactions
- Bootstrap 5 via CDN for styling
- SortableJS via CDN for drag-and-drop
- Font Awesome via CDN for icons
- Templates located in `anthias_app/templates/anthias_app/`

### Qt/C++ (WebView)
- Use macros for Qt5/Qt6 cross-version compatibility

## API

The REST API is at `/api/v2/`. The v2 API (in `api/views/v2.py`) uses DRF with drf-spectacular for OpenAPI schema generation. API docs at `/api/docs/`.
