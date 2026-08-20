# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anthias is an open-source digital signage platform for Raspberry Pi and x86 PCs (formerly Screenly OSE). It manages and displays media assets (images, videos, web pages) on connected screens.

## Architecture

Anthias runs as a set of Docker containers:

- **anthias-server** (port 80 in prod, 8000 in dev) — uvicorn (ASGI) serving the Django web app (server-rendered templates with Alpine.js + htmx), REST API, the frontend's static assets (thin TypeScript bundles built by bun, served via WhiteNoise), uploaded media at `/anthias_assets/`, and the WebSocket endpoint at `/ws` (Django Channels with a Redis-backed channel layer). Always plain HTTP — TLS is opt-in and handled by the **anthias-caddy** sidecar that `bin/enable_ssl.sh` installs as a compose override (Caddy local CA by default, or auto Let's Encrypt with `--domain`, or BYO cert with `--cert`/`--key`).
- **anthias-celery** — Async task queue (asset downloads, cleanup). Runs the same image as `anthias-server` with a CMD override that starts the Celery worker; the two services share the entire root filesystem to avoid duplicating ~825 MB of identical apt content per device. Publishes asset-update events back to the WebSocket consumers via the Channels Redis layer.
- **anthias-viewer** — Drives the display, receives instructions over the Redis pub/sub `anthias.viewer` channel, talks to anthias-server over HTTP.
- **redis** (port 6379) — Celery broker + result backend, Channels channel layer, and the viewer signalling bus (pub/sub channel + per-correlation-ID reply lists).
- **webview** — Qt-based browser for rendering content on the display; fetches `/anthias_assets/` from anthias-server.

Inter-service messaging is all Redis: WebSocket fan-out from Celery to browsers goes via Channels/Redis, and server↔viewer commands/replies use Redis pub/sub on `anthias.viewer` with BLPOP on `anthias.reply.<correlation-id>` for the few request-reply paths. The viewer also publishes plain facts the server reads directly (CEC availability, SMART, and the now-playing asset) rather than answering a round trip per render. The primary database is SQLite stored at `~/.anthias/anthias.db`, with configuration in `~/.anthias/anthias.conf`. (Pre-rebrand installations have these at `~/.screenly/screenly.db` and `~/.screenly/screenly.conf`; `bin/migrate_legacy_paths.sh` migrates them on upgrade and leaves back-compat symlinks.)

### Key Directories

All application code lives under `src/` (moved there from the old top-level `anthias_app/`, `anthias_django/`, `api/`, `static/src/`, `viewer/`, `lib/`).

- `src/anthias_server/` — the Django server: `app/` (server-rendered templates + Alpine.js/htmx, views, consumers, models, migrations, management commands, and `app/static/src/` TypeScript bundles), `api/` (REST API views/serializers/URLs for v1, v1.1, v1.2, v2), `django_project/` (settings, URLs, ASGI/WSGI), `lib/`
- `src/anthias_common/` — shared Python utilities (Redis, board/low-RAM helpers, device helpers)
- `src/anthias_viewer/` — Viewer service (scheduling, media player, Redis pub/sub messaging)
- `src/anthias_webview/` — C++ Qt WebView (Qt5 for the 32-bit armhf boards Pi 1/2/3; Qt6 for the arm64/x86 boards Pi 3-64/4/5, x86, Rock Pi 4)
- `src/anthias_host_agent/` — host-side agent that publishes host facts (memory, board subtype) into Redis for the containers
- `docker/` — Dockerfile Jinja2 templates for each service
- `tests/` — Python unit/integration tests
- `bin/` — Shell scripts for install, dev setup, testing, upgrades
- `tools/` — Utilities including Docker image builder (`tools.image_builder`)

### Project knowledge base

Deep, hard-won operational and hardware knowledge distilled from long-running debugging lives in committed skills under `.claude/skills/` (auto-discovered on clone; `.gitignore` un-ignores this path) — start at `.claude/MEMORY.md`:

- **testbed-qa** — validating changes on the physical hardware fleet
- **anthias-hardware** — per-board display/codec/rotation/audio internals
- **anthias-viewer** — viewer/server app internals and subtle bug root-causes
- **anthias-release** — balena/CalVer/CI/Sentry/telemetry operations (reference)
- **cut-release** — step-by-step runbook for cutting a tagged release

## Development Commands

### Dev Environment

```bash
./bin/start_development_server.sh                    # Start full dev environment (Docker)
docker compose -f docker-compose.dev.yml down        # Stop dev server
# Web UI at http://localhost:8000
```

### Frontend (TypeScript + Alpine.js/htmx)

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

Integration tests (`-m integration`) drive Playwright (sync API)
against Chromium and still require the Docker stack; use the recipe
below.

#### Docker-based runs (CI parity, integration suite)

```bash
# Build and start test containers
uv run python -m tools.image_builder --dockerfiles-only --disable-cache-mounts --service redis --service test
docker compose -f docker-compose.test.yml up -d --build

# Prepare and run tests (integration and non-integration must be run separately)
docker compose -f docker-compose.test.yml exec anthias-test bash ./bin/prepare_test_environment.sh -s
docker compose -f docker-compose.test.yml exec anthias-test pytest -n auto -m "not integration"
# ANTHIAS_INTEGRATION_TEST=1 pins TEST.NAME to the same SQLite file the
# anthias-server container writes — required for Playwright tests that
# assert on Asset.objects after a browser-driven upload.
# --reuse-db skips pytest-django's destroy-and-recreate cycle so
# uvicorn's open handle stays valid; prepare_test_environment.sh has
# already applied migrations.
docker compose -f docker-compose.test.yml exec -e ANTHIAS_INTEGRATION_TEST=1 anthias-test pytest -m integration --reuse-db

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

### TypeScript
- The UI is server-rendered Django templates driven by Alpine.js + htmx; TypeScript is thin page bundles (`home.ts`, `apps.ts`, `splash.ts`, `vendor.ts`) under `src/anthias_server/app/static/src/`, built by bun. There is no React/Redux SPA.
- No `any` or `unknown` types
- Import order: built-in → third-party → local (alphabetically sorted, blank line between groups)
- Use `rem` instead of `px` in SCSS

### Qt/C++ (WebView)
- Use macros for Qt5/Qt6 cross-version compatibility

### Django templates
- `{# … #}` only comments out a single line. Anything that wraps to the next line renders verbatim in the page. Use `{% comment %}…{% endcomment %}` for any comment that does not fit on one line.

## Working conventions

Process and workflow rules for this repo (the deeper "why" for each lives in the knowledge-base skills):

- **Fix the root cause; no hacky fallbacks.** Anthias must work out of the box. Prefer fixes at the install / bootstrap / startup-ordering layer (or upstream) over hardcoded values baked into ship-to-customer config. If only a per-container hardcode is possible, surface the tradeoff explicitly.
- **Never `network_mode: host`** for any service. Keep bridge isolation; route host-only data (IPs, MACs, interfaces) via the host agent → Redis.
- **Never break the REST API.** v1, v1.1, v1.2, and v2 are all supported; request/response shape, status codes, and field semantics stay stable per version. A wire-shape change goes through a new API version, never a mutation of an existing one.
- **No `#NNN` in PR bodies.** GitHub auto-links every `#NNN` to a PR/issue and silently cross-references the wrong one. Spell out external references; reserve `#NNN` for a genuine PR/issue in this repo.
- **No `# noqa` / `# type: ignore` suppressions** when a proper idiom fixes the root cause (`from mod import name as name` for re-exports, `_arg` for unused callback args, `__all__` for star-imports).
- **Don't game linters — annotate genuine false positives.** For a real SonarCloud false positive use inline `# NOSONAR` with a reason (http-to-localhost is a safe `python:S5332` FP). Never contort working code to dodge a rule.
- **Run BOTH `ruff check` AND `ruff format --check`** before any Python push — CI runs both. Also `uv run mypy .` project-wide.
- **Open PRs ready-for-review, not draft** (drafts don't trigger Copilot review).
- **Copilot review loop:** re-request via `gh pr edit <pr> --add-reviewer copilot-pull-request-reviewer` (pushes don't auto-trigger); never post `@Copilot` trigger/summary comments; after every push pull Copilot's comments AND resolve the threads the push addressed; iterate until a fresh pass returns no actionable comments.
- **Forum replies (forums.screenly.io): plain, non-technical language.** Lead with what it means for the user, drop jargon, keep it short and warm. No em-dashes. Always link PRs/releases as markdown links, never bare `#NNN`/version text. Only mark Solved what the code confirms.
- **US English website copy** (color, customization, organize/recognize). Paraphrase British third-party UI labels rather than quoting them.
- **New website scripts/bundles are TypeScript** (`.ts`). Apply PageSpeed guardrails by default: defer JS, lazy-load non-LCP images, preload the LCP image WebP-to-WebP only, explicit width/height + `aspect-ratio` for CLS, respect `prefers-reduced-motion`.
- **Audit whole log spans holistically, not naive grep** — a fixed marker set only finds failure modes you predicted. Verify the positive (is it actually playing the right content?), compare before vs after, and treat first-few-minutes post-OTA crash-loops as possible restart transients (re-check current state before flagging).
- **No device identifiers in public CI logs** — Actions logs are world-readable; scripts default to aggregate-only output, per-device detail behind a local-only `--verbose` flag.

## API Versions

The REST API has multiple versions at `/api/v1/`, `/api/v1.1/`, `/api/v1.2/`, and `/api/v2/`. The v2 API (in `src/anthias_server/api/views/v2.py`) is the current primary API using DRF with drf-spectacular for OpenAPI schema generation.
