---
title: "API Reference - Anthias Digital Signage"
description: "REST API reference for Anthias. Endpoints for managing assets, device settings, backups, and integrations."
layout: api
---

## Overview

Anthias exposes a REST API so you can manage a player without touching the web
interface: script asset uploads, rotate playlists, trigger backups, or wire the
device into your own tooling. Everything the dashboard does is available over
HTTP, which makes Anthias straightforward to automate across a fleet of screens.

The API runs directly on each device, so the base URL is simply the player's own
address on your network, under `/api/`:

```
http://<device-ip>/api/
```

That is port 80 on a normal install; a development environment serves the same
API on port 8000. If you have enabled SSL on the device, use `https://` with the
hostname on the certificate instead. Requests and responses are JSON, except for
asset uploads, which use `multipart/form-data`.

## Versioning

The API is versioned in the path, and several versions are served side by side so
existing integrations keep working as new ones are added:

- **v2** (`/api/v2/…`): the current version. Use this for new integrations. It
  exposes the full asset model, including the scheduling fields (`play_order`,
  duration, and the active date window).
- **v1, v1.1, v1.2** (`/api/v1/…`, `/api/v1.1/…`, `/api/v1.2/…`): retained for
  backwards compatibility. Their request and response shapes stay stable, but the
  older versions do not expose the newer scheduling fields.

Pin your integration to a specific version so a future release cannot change the
shape of the responses you depend on.

## Authentication

A fresh install serves the API without authentication, on the assumption that it
sits on a trusted local network. Switch that on from **Settings → Authentication**
in the dashboard, or over this API by PATCHing `auth_backend` on
`/api/v2/device_settings`; headless clients then authenticate with HTTP Basic.
[Anthias for AI agents](/agents/) walks through enabling it and the behavior to
expect, including the fact that an unauthenticated call redirects to the login
form rather than returning `401`.

Either way, do **not** expose a device's HTTP port directly to the public
internet. Basic credentials travel in the clear over plain HTTP, so keep the
device behind your LAN or a VPN, or put a reverse proxy with its own access
control in front of it.

## Responses and errors

Successful requests return a `2xx` status with a JSON body. Client mistakes (a
missing field, a malformed asset) return `4xx` with a JSON error describing what
went wrong; unexpected server-side failures return `5xx`. Each endpoint below
lists the response codes it can return and the schema of the body that comes with
them. Expand a response to see the full field list.

## Endpoints

The complete endpoint reference follows, grouped by tag. Each entry shows the
method, path, parameters, request body, and every response it can return.
