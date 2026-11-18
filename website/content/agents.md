---
title: "Anthias for AI Agents"
description: "How to drive Anthias digital signage from an AI agent: install a player over SSH, turn on API authentication, then upload images, videos and web pages and schedule them across any number of screens over a plain REST API."
---

Anthias is open-source digital signage that an agent can operate end to end. Every
player is a self-contained Linux device that serves its own REST API on your
network, so there is no cloud tenant to model, no OAuth dance and no SDK to
install — a screen is a base URL, and a fleet is a list of them.

This page is written to be read by an agent. The requests below are complete and
copy-pasteable, and each one was run against a real device.

## The short version

1. **Install Anthias** on a Raspberry Pi or x86 box. An agent with SSH access can
   do this itself.
2. **Turn on authentication** so the API is not open to everyone on the LAN.
3. **Upload images, videos and web pages** over the API and schedule them — on one
   screen or on fifty, by looping over the list.

## What an agent is talking to

| | What it is |
| --- | --- |
| Base URL | `http://<device-ip>/api/v2/` (port 80; a dev server uses 8000) |
| Format | JSON, except uploads, which are `multipart/form-data` |
| Machine-readable schema | `http://<device-ip>/api/schema/` — the device's own OpenAPI document |
| Human-readable reference | [anthias.screenly.io/api/](/api/), and `http://<device-ip>/api/docs/` on the device |
| Versions | `v2` (use this), plus `v1`, `v1.1` and `v1.2` kept for compatibility |

Fetch `/api/schema/` first if you want the authoritative field list for the exact
version running on that device — it is generated from the server's own
serializers, so it cannot drift from the running code. It stays readable without
credentials even when authentication is on, so it is safe to use as a capability
probe. `/api/docs/`, the rendered reference, follows the normal auth rules.

There is no central server and no cross-device identity. Each player holds its own
assets, its own settings and its own `asset_id` values, and the same image pushed
to three devices gets three unrelated ids. Treat the device list as the unit of
work and reconcile by `name`, not by id.

## 1. Install a player

On a Raspberry Pi running Raspberry Pi OS Lite (64-bit recommended), or on a
Debian x86 machine, the installer is one command:

```bash
$ bash <(curl -sL https://install-anthias.srly.io)
```

It is an interactive installer, not a silent one. It asks four questions — confirm
the install, whether Anthias should manage the network, which version to install,
and whether to run a full system upgrade — through full-screen dialogs, so it
needs a terminal. An agent driving it over SSH must allocate a PTY and answer the
prompts:

```bash
$ ssh -t pi@<device-ip> 'bash <(curl -sL https://install-anthias.srly.io)'
```

Expect it to take anywhere from 15 minutes to several hours, depending on the
board, the SD card and the connection, so run it with a generous timeout rather
than a short one. For hands-off provisioning at scale, prefer a route that needs
no prompts at all: flash a [pre-built disk image](/get-started/), pick Anthias in
[Raspberry Pi Imager](https://www.raspberrypi.com/software/) under *Other
specific-purpose OS → Digital signage and kiosks*, or deploy a
[Balena fleet](/docs/balena/) over the air. All the options are listed in
[installation options](/docs/install/).

Poll for readiness instead of guessing when it is done:

```bash
$ curl -fsS http://<device-ip>/api/v2/info
```

A `200` with an `anthias_version` field means the player is up and the API is
serving. The same response carries `free_space`, `uptime`, `memory`,
`device_model` and under-voltage and storage health — a good periodic check for an
agent that is minding a fleet.

## 2. Turn on API authentication

A fresh install has authentication off: anything on the network can read and write
the API. Before you point an agent at a device — and certainly before you put
anything real on the screen — switch it on.

Enabling it is one request. On a device that has never had an operator account,
no current password is needed:

```bash
$ curl -X PATCH http://<device-ip>/api/v2/device_settings \
    -H 'Content-Type: application/json' \
    -d '{
          "auth_backend": "auth_basic",
          "username": "agent",
          "password": "<strong-password>",
          "password_2": "<strong-password>"
        }'
{"message":"Settings were successfully saved."}
```

Once an operator account exists, every later change to the authentication method
or the credentials must also carry `current_password`, including re-enabling after
a period with auth switched off:

```bash
$ curl -X PATCH http://<device-ip>/api/v2/device_settings \
    -H 'Content-Type: application/json' \
    -d '{
          "auth_backend": "auth_basic",
          "current_password": "<old-password>",
          "username": "agent",
          "password": "<new-password>",
          "password_2": "<new-password>"
        }'
```

A wrong or missing current password returns `400` with an `error` field, not a
silent no-op. Sending `"auth_backend": ""` with the correct `current_password`
turns authentication back off.

From then on, authenticate with HTTP Basic on every call:

```bash
$ curl -u agent:<strong-password> http://<device-ip>/api/v2/assets
```

Three things worth knowing before you write the client:

- **An unauthenticated API call answers `302`, not `401`.** It redirects to the
  browser login form at `/login/`. A client that follows redirects by default
  (`curl -L`, `requests` without `allow_redirects=False`) will turn a rejected
  request into a `200` with an HTML login page, which is a confusing way to find
  out your credentials were missing. Disable redirect-following and treat anything
  that is not a `2xx` as a failure. Wrong credentials give a clean `401`.
- **Basic auth is served over plain HTTP by default**, so the credentials cross
  the network in reversible encoding. Keep the device on a trusted LAN or VPN,
  never expose port 80 to the internet, and run `./bin/enable_ssl.sh` on the
  device if you want TLS, after which use `https://` and the hostname on the
  certificate.
- **Basic is the supported headless path today, and the server logs a deprecation
  warning each time it is used** (throttled, so a polling client does not flood
  the log). It keeps working; a token-based scheme is intended to replace it
  later. Pin your integration to `v2` and read `/api/schema/` on each device so a
  future change is something you detect rather than something that surprises you.

## 3. Put content on the screen

An asset is one item in the player's loop. Creating one is a `POST`; the required
fields are `name`, `uri`, `mimetype`, `start_date`, `end_date`, `duration` and
`is_enabled`.

### A web page or dashboard

```bash
$ curl -X POST http://<device-ip>/api/v2/assets \
    -u agent:<strong-password> \
    -H 'Content-Type: application/json' \
    -d '{
          "name": "Status board",
          "uri": "https://status.example.com/",
          "mimetype": "webpage",
          "duration": 30,
          "is_enabled": true,
          "start_date": "2026-01-01T00:00:00Z",
          "end_date": "2026-12-31T23:59:59Z"
        }'
```

The response is the stored asset, including the `asset_id` you will need for
later updates. On create you may also send `refresh_interval_s` to make the page
reload on its own, and `custom_headers` for a page behind a header-based token.

### An image or a video file

Uploading is two steps: push the bytes, then create the asset from what the upload
handed back.

```bash
$ curl -X POST http://<device-ip>/api/v2/file_asset \
    -u agent:<strong-password> \
    -F 'file_upload=@poster.png'
{"uri":"/data/anthias_assets/b1d6….tmp","ext":".png","upload_id":"b1d6…"}
```

Pass that `uri` and `ext` straight through to the create call:

```bash
$ curl -X POST http://<device-ip>/api/v2/assets \
    -u agent:<strong-password> \
    -H 'Content-Type: application/json' \
    -d '{
          "name": "Autumn poster",
          "uri": "/data/anthias_assets/b1d6….tmp",
          "ext": ".png",
          "mimetype": "image",
          "duration": 10,
          "is_enabled": true,
          "start_date": "2026-10-01T00:00:00Z",
          "end_date": "2026-11-30T00:00:00Z"
        }'
```

The server moves the file into place and returns the asset with its final `uri`.
Large files can be sent in chunks with `Content-Range`, echoing the `upload_id`
back in an `X-Upload-Id` header on every chunk after the first.

### YouTube and live streams

| What you want | `mimetype` | `uri` |
| --- | --- | --- |
| Image | `image` | uploaded file |
| Video file | `video` | uploaded file, or an `http(s)` URL to download |
| Web page | `webpage` | any `http(s)` URL |
| Live stream | `streaming` | RTSP / HLS / DASH URL |
| YouTube video | `youtube_asset` | the YouTube watch URL |

A `youtube_asset` is downloaded on the device so it plays locally, without
buffering, ads or recommendations. The row comes back immediately with
`mimetype` rewritten to `video` and `is_processing: true`; the real duration lands
when the download finishes. Remote video URLs follow the same path, and uploaded
videos may be re-encoded for the board. **An asset with `is_processing: true` is
skipped by the viewer until the work completes**, so poll
`GET /api/v2/assets/<asset_id>` until the flag clears if you need to confirm a
video is really on air. Nothing on screen is interrupted while that happens.

## Scheduling

Scheduling is per asset, so content loaded once manages itself.

| Field | Meaning |
| --- | --- |
| `start_date`, `end_date` | The window the asset is eligible to play at all |
| `play_days` | ISO weekdays, Monday `1` through Sunday `7`. Omit for every day |
| `play_time_from`, `play_time_to` | Daily window, `HH:MM:SS` local to the device. A `from` later than `to` means an overnight window, and `play_days` refers to the day it starts |
| `duration` | Seconds on screen per rotation |
| `play_order` | Position in the loop |
| `is_enabled` | Whether the asset is in the playlist at all |
| `is_active` | Read-only: whether it is playing *right now*, given all of the above |

```bash
$ curl -X PATCH http://<device-ip>/api/v2/assets/<asset_id> \
    -u agent:<strong-password> \
    -H 'Content-Type: application/json' \
    -d '{
          "play_days": [1, 2, 3, 4, 5],
          "play_time_from": "09:00:00",
          "play_time_to": "17:00:00"
        }'
```

Set the whole loop order in one call with a comma-separated list of ids:

```bash
$ curl -X POST http://<device-ip>/api/v2/assets/order \
    -u agent:<strong-password> \
    -H 'Content-Type: application/json' \
    -d '{"ids": "<asset_id_1>,<asset_id_2>,<asset_id_3>"}'
```

And jump the viewer immediately, which is useful when a human is standing in front
of the screen asking to see the thing you just uploaded:

```bash
$ curl -u agent:<strong-password> \
    'http://<device-ip>/api/v2/assets/control/next'
$ curl -u agent:<strong-password> \
    'http://<device-ip>/api/v2/assets/control/asset&<asset_id>'
```

`previous` works the same way.

## Scheduling across N screens

There is no fan-out endpoint, because there is no central server — you loop. That
is the whole mechanism, and it is worth stating plainly, because it is what makes
the fleet an agent's problem rather than a platform's.

```python
import requests

DEVICES = ['10.0.0.11', '10.0.0.12', '10.0.0.13']
AUTH = ('agent', '<strong-password>')


def publish(path: str, name: str, **fields) -> dict[str, str]:
    results = {}
    for host in DEVICES:
        base = f'http://{host}/api/v2'
        try:
            with open(path, 'rb') as fh:
                up = requests.post(
                    f'{base}/file_asset',
                    files={'file_upload': fh},
                    auth=AUTH,
                    allow_redirects=False,
                    timeout=120,
                )
            up.raise_for_status()
            upload = up.json()

            created = requests.post(
                f'{base}/assets',
                json={
                    'name': name,
                    'uri': upload['uri'],
                    'ext': upload['ext'],
                    'mimetype': 'image',
                    'duration': 15,
                    'is_enabled': True,
                    'start_date': '2026-11-01T00:00:00Z',
                    'end_date': '2026-11-30T00:00:00Z',
                    **fields,
                },
                auth=AUTH,
                allow_redirects=False,
                timeout=60,
            )
            created.raise_for_status()
            results[host] = created.json()['asset_id']
        except requests.RequestException as exc:
            results[host] = f'failed: {exc}'
    return results
```

Four habits make this reliable:

- **Report per device.** A fan-out is a set of independent outcomes, not one. Say
  which screens took the content and which did not, rather than collapsing it into
  a single success.
- **Reconcile by `name`.** Ids are per device. `GET /api/v2/assets` and match on
  the name you chose, so a re-run updates the existing asset instead of stacking
  duplicates on the loop.
- **Verify, don't assume.** A `201` means the row exists. `is_active` on a later
  `GET` means it is genuinely on screen, and `is_processing` tells you a video is
  still being prepared.
- **Keep the timeout generous on uploads.** A large video over Wi-Fi to a Pi is not
  a fast request.

## Other endpoints an agent will reach for

| Endpoint | Use |
| --- | --- |
| `GET /api/v2/info` | Version, uptime, free space, memory, storage health — fleet monitoring |
| `GET /api/v2/assets` | What is on this device right now |
| `DELETE /api/v2/assets/<asset_id>` | Remove an item |
| `PATCH /api/v2/device_settings` | Player name, timezone, default durations, screen rotation, authentication |
| `POST /api/v2/display/{on,off}` | Switch the display (experimental, needs HDMI-CEC) |
| `POST /api/v2/backup` / `POST /api/v2/recover` | Snapshot a configured device and clone it onto the next one |
| `POST /api/v2/reboot`, `POST /api/v2/shutdown` | Power control |
| `POST /api/v2/integrations/import/{provider}/item` | Pull content in from Yodeck, ScreenCloud, piSignage or Xibo |

`backup` and `recover` are the fastest honest way to stand up screen number two:
configure one device properly, take the backup, restore it on the rest, then let
the agent manage content per device from there.

## Limits worth knowing up front

Anthias manages content **per device**. There is no fleet-wide playlist, no
grouping, no role model and no audit trail — an agent that wants those keeps them
on its own side. Credentials are a single operator account per device, so there is
no separate machine identity to revoke if you want to cut an agent off; rotating
the password cuts off everything, the dashboard included. Scheduling is dates,
weekdays and a daily window, evaluated in the device's local timezone, which is
worth setting explicitly via `device_settings` on a geographically spread fleet.

None of that is in the way of the thing this page is about. Driving screens over a
small HTTP API on hardware you own, with no account and no per-screen fee, is
exactly the shape of problem an agent is good at.

## Where to go next

- [API reference](/api/) — every endpoint, parameter and response
- [Get Started](/get-started/) — all the installation routes
- [Asset scheduling](/docs/asset-scheduling/) — the scheduling model in detail
- [Documentation](/docs/) — operations, logs, fleet deployment
- [`llms.txt`](/llms.txt) and [`llms-full.txt`](/llms-full.txt) — this whole site as
  Markdown, for ingestion
- [github.com/Screenly/Anthias](https://github.com/Screenly/Anthias) — the source
