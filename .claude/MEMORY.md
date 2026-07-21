# Anthias — seed memory

This is a curated, PII-scrubbed seed of hard-won project knowledge, distilled
from a working agent's memory before its VM was retired. A fresh clone starts
here. It is **not** an auto-memory index — it is a static handoff. Everything
below is also captured, in depth, in the committed skills under
`.claude/skills/`; this file is the map.

> `.claude/` is mostly gitignored (worktrees + unfiltered local memory), but
> `.gitignore` explicitly un-ignores `.claude/MEMORY.md` and `.claude/skills/`
> so this curated handoff tracks normally (no `git add -f` needed) — alongside
> the pre-existing `commit`/`create-pr` skills. Keep them scrubbed: **this is a
> public repo — never add IPs, usernames, emails, device UUIDs, tokens, ngrok
> URLs, or forum handles.**

## Skills (read the one that matches your task)

- **[testbed-qa](skills/testbed-qa/SKILL.md)** — validating changes on the
  physical hardware fleet: the `/tmp/used-by` lockfile protocol, docker-compose
  (not balena) deploy, the pure-Python overlay method, force-display on headless
  eglfs, and per-board memory/hardware quirks.
- **[anthias-hardware](skills/anthias-hardware/SKILL.md)** — per-board display
  stack (linuxfb/eglfs/wayland-cage), video HW-vs-SW decode per chip, the Qt6
  presentation bottleneck, rotation, WebGL, splash, audio, the Pi 2 armhf SIGBUS.
- **[anthias-viewer](skills/anthias-viewer/SKILL.md)** — viewer/server internals:
  server-rendered Django/ASGI + Redis pub/sub, streaming-under-ASGI, WAL playlist
  reload, viewer OOM behavior, upload codec gate, Sentry triage, webview C++,
  PulseAudio, content-import framework.
- **[anthias-release](skills/anthias-release/SKILL.md)** — balena cloud API +
  OS track topology, CalVer stamping + release/OTA sequencing, image builder +
  toolchain, CI behavior, Sentry conventions, telemetry (the reference facts).
- **[cut-release](skills/cut-release/SKILL.md)** — the actionable step-by-step
  runbook for cutting a tagged release: bump the version across all manifests +
  lockfiles, merge, wait for the master Docker build, tag, and publish (auto
  notes first, then a curated summary).

## Highest-value cross-cutting facts (the things easiest to get wrong)

- **The repo is server-rendered Django, not React.** UI = templates +
  Alpine.js/htmx under `src/anthias_server/app/`; thin TS bundles built by bun.
  Everything moved under `src/` (`anthias_common`, `anthias_server`,
  `anthias_viewer`, `anthias_webview`, `anthias_host_agent`). See CLAUDE.md.
- **Board → rendering stack is chosen by userspace arch** (`dpkg
  --print-architecture`), not `uname -m` — a 32-bit Pi OS ships a 64-bit kernel.
- **Only Qt6 boards (Pi 3-64/4/5, x86, Rock Pi 4) do HW video + WebGL.** Qt5
  linuxfb boards (Pi 1/2/3-32) have no GL and use GStreamer→fbdevsink.
- **Rotation is clockwise on every stack** as of 2026.07.3; the mechanism differs
  per QPA (linuxfb hand-rotates, eglfs negates, wayland uses wlr transforms).
- **1 GB boards OOM-wedge the latest viewer even idle; 512 MB is unusable** for
  webpages. Read "wedge/unresponsive" on a small board as memory pressure first.
- **CI unit tests run on the ephemeral merge SHA**, so head-SHA check-runs look
  empty — find them via `gh run list`.
- **CalVer resets MICRO each month**; balena needs no-leading-zero semver in
  `balena.yml`; cut the GH release only AFTER the master Docker build is green.

## Working conventions

The durable working conventions (fix-root-cause, no `network_mode: host`, no
`#NNN` in PR bodies, no `noqa`, run `ruff format --check`, the Copilot review
loop, never-break-API, forum-reply tone, US-English website, etc.) live in
**CLAUDE.md → "Working conventions"** so they load every session.

## Rebuilding live auto-memory on a fresh VM

If you use the file-based auto-memory system, treat this file and the skills
under `.claude/skills/` as the source of truth to re-seed from. Do not copy any environment-specific
state (testbed IP↔board mapping, SSH user, tunnel URLs) into the repo — that is
local operator state, kept only on the operator's machine.
