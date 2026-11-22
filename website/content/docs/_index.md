---
title: "Documentation"
description: "Anthias documentation: installation, deployment, development, and operations."
---

## How to get logs from Anthias

Every Anthias service sends its output to the host's `systemd-journald`,
so the system handles rotation and retention and nothing writes unbounded
`*-json.log` files under `/var/lib/docker/containers/`. You can read those
logs from the REST API without logging in to the device, or over SSH with
`journalctl` or `docker`.

| Service | Container name | Journal tag | What it does |
| ------- | -------------- | ----------- | ------------ |
| `anthias-server` | `anthias-anthias-server-1` | `anthias-server` | uvicorn: web UI, REST API, WebSocket at `/ws`, static and media files |
| `anthias-caddy` | `anthias-anthias-caddy-1` | `anthias-caddy` | Caddy, the web front end on ports 80 and 443 (HTTPS, routing) |
| `anthias-viewer` | `anthias-anthias-viewer-1` | `anthias-viewer` | Viewer service and the WebView that draw the screen |
| `anthias-celery` | `anthias-anthias-celery-1` | `anthias-celery` | Celery worker (asset downloads, video conversion, cleanup) |
| `redis` | `anthias-redis-1` | `anthias-redis` | Redis (Celery broker, Channels layer, viewer messaging) |
| `anthias-wifi-connect` | `anthias-wifi-connect` | `anthias-wifi-connect` | WiFi setup portal; only runs while the device has no network |

One piece runs on the host rather than in a container: the
`anthias-host-agent` systemd unit, which handles reboot and shutdown,
publishes host facts, and answers the log API below.

### From the REST API

`GET /api/v2/system/logs` returns a snapshot of the host journal as JSON,
on both Raspberry Pi OS / Debian installs and balena. It takes the same
credentials as the rest of the API: when authentication is on, create a
token under **Settings → API tokens** and send it as a bearer token.

```bash
# The newest 200 lines from the server container
$ curl -sk -H "Authorization: Bearer ${TOKEN}" \
    "https://<device-ip>/api/v2/system/logs?container=anthias-anthias-server-1&count=200"

# The last hour of the Docker daemon
$ curl -sk -H "Authorization: Bearer ${TOKEN}" \
    "https://<device-ip>/api/v2/system/logs?unit=docker.service&since=-1h"
```

The query parameters are:

* `count`: number of lines, newest last. Defaults to 500, capped at 2000.
* `container`: one container. On Raspberry Pi OS / Debian use the
  container name from the table above (`anthias-anthias-server-1`); on
  balena use the service name (`anthias-server`).
* `unit`: one systemd unit, e.g. `docker.service` or `anthias-host-agent.service`.
* `since` / `until`: any time spec `journalctl` accepts, e.g. `-1h`,
  `yesterday`, or `2026-10-26 09:00`.

It is a snapshot, not a live tail: request it again to see newer lines.
Secret-looking values (WiFi passphrases, tokens) are redacted. The
response's `persistent` field tells you whether the journal survives a
reboot (see [Keeping logs across reboots](#keeping-logs-across-reboots)).
The full schema is in the [API reference](/api/).

### Using `journalctl`

SSH into the device. For instance, if you've set `pi` for the username
and `raspberrypi` for the hostname:

```bash
$ ssh pi@raspberrypi
```

Each container is tagged in the journal, so you can filter on the tag
without going through Docker. This is also the way to combine container
logs with system logs, or search a long time range:

```bash
$ sudo journalctl -f -t anthias-server
$ sudo journalctl --since "1 hour ago" -t anthias-viewer
$ sudo journalctl -u anthias-host-agent
```

The tags are in the table above.

### Using `docker logs` or `docker compose logs`

The journald driver still supports `docker logs`, by container name:

```bash
$ docker logs -f anthias-anthias-server-1
```

Or by service name with Docker Compose, from the Anthias directory:

```bash
$ cd ~/anthias
$ docker compose logs -f anthias-server
```

### On balena

balena doesn't apply the journal tags above. Use the logs panel for the
device in the [balenaCloud dashboard](https://dashboard.balena-cloud.com/),
`balena logs <device-uuid>` from the balena CLI, or the REST API.

### Keeping logs across reboots

Raspberry Pi OS keeps the journal in memory only (`Storage=volatile`), so
logs from before the last reboot are gone, and Anthias leaves it that
way: a persistent journal writes 20-30 MB a day to the SD card, which
wears it out. The REST API and `journalctl` still work, they just start
at the current boot.

If you need history across reboots (an x86 player booting from an SSD,
or a device you are debugging), turn persistence on with a journald
drop-in that caps the size and sorts after Raspberry Pi OS's own
`40-rpi-volatile-storage.conf`:

```bash
$ sudo mkdir -p /var/log/journal /etc/systemd/journald.conf.d
$ sudo systemd-tmpfiles --create --prefix /var/log/journal
$ printf '[Journal]\nStorage=persistent\nSystemMaxUse=64M\n' | \
    sudo tee /etc/systemd/journald.conf.d/90-persistent-journal.conf
$ sudo systemctl restart systemd-journald
```

Use a file name other than `99-anthias.conf`: the installer manages
that one and removes it on upgrade unless its own
`anthias_journal_persistent` Ansible variable is set. Delete the drop-in
and restart journald to go back to the default.

To free space immediately:

```bash
$ sudo journalctl --vacuum-time=2d   # drop entries older than 2 days
$ sudo journalctl --vacuum-size=64M  # cap the journal at 64 MB
```

> **Note**
>
> Devices upgraded from a release that used Docker's `json-file` driver
> may still have the old log files on disk. To reclaim that space,
> truncate them in place (Docker keeps them open, so deleting can
> confuse the daemon; `truncate -s 0` is safe):
>
> ```bash
> $ sudo find /var/lib/docker/containers/ -name "*-json.log" \
>     -exec truncate -s 0 {} +
> ```

## Enabling SSH

See [the official documentation](https://www.raspberrypi.org/documentation/remote-access/ssh/)

## Updating Anthias

Run the following command in your console:

```bash
$ bash <(curl -sL https://install-anthias.srly.io)
```

Alternatively, you can also run the following command:

```bash
$ $HOME/anthias/bin/run_upgrade.sh
```

Before starting the new release, the upgrade checks that every image it
pulled was signed by the Anthias release pipeline (and the WiFi setup
image by its own upstream release). If a check fails, the upgrade stops
and the device keeps running the release it already had.

## Accessing the REST API

The full endpoint reference is on the [API page](/api/): endpoints, parameters, and response schemas grouped by tag.

If you'd prefer the live ReDoc-rendered docs straight from your device, open `http://<device-ip>/api/docs/` (or `http://localhost:8000/api/docs/` in development mode).

### Authenticating with API tokens

When **Authentication** is turned on in Settings, scripts and other devices should use an API token instead of your username and password.

1. Go to **Settings → API tokens**, give the token a name (for example, the machine that will use it), and click **Create token**.
2. Copy the token right away. Anthias stores only a hash of it, so it can't be shown again.
3. Send it in the `Authorization` header:

```bash
$ curl -H 'Authorization: Bearer anthias_…' https://<device-ip>/api/v2/info
```

Until you [upload a trusted certificate](#tls--ssl), the device uses its own self-signed one, so add `-k` (or `--cacert`) to `curl`.

Tokens are only accepted over HTTPS. A request that sends one over plain `http://` gets `401 Unauthorized`; if that happened on a network you don't trust, revoke the token. A token can manage content, playback and the screen through the API, but it can't sign in to the web interface, create more tokens, or create or restore backups (a backup holds the device's secret key and your password hash). Use the web interface for backups. To cut off access, click **Revoke** next to the token; anything still using it gets `401 Unauthorized` from then on. Changing your password does not revoke tokens. Restoring a backup revokes every token and ends multi-screen management, so create the tokens you need again afterwards.

Tokens only apply while authentication is on:

* With **Authentication** set to **Disabled**, the API is open to anyone on your network. You can't create tokens in that mode, because they would not protect anything, and a token sent in a request is ignored.
* Tokens you created earlier stay listed (and can still be revoked) but are inactive until you turn authentication back on.

HTTP Basic auth (`curl -u user:pass …`) still works for older clients but is deprecated in favor of tokens. A backup created through `POST /api/v2/backup` can be downloaded for one hour, by a signed-in operator or with Basic auth.

### Managing several screens from one (multi-screen)

One Anthias device (the controller) can publish the same playlist to the other Anthias screens on your network, show their status and health, and reboot or control them. Screens join with **join information** copied from the controller, which pins the controller's certificate, and are only managed once you approve them on the controller. The two sides then talk over a separate management port, **8443**, using mutual TLS with certificates the controller issues. A managed screen's own web interface is turned off; you change it from the controller.

See [Multi-screen management](/docs/multi-screen/) for the full reference: which devices to use as controller and as screens, setup, publishing, health monitoring, security, and troubleshooting.

## TLS / SSL

Anthias supports two independent SSL features:

### 1. Serving HTTPS

Every install runs Caddy (`anthias-caddy`) in front of `anthias-server`.
Caddy owns host ports 80 and 443; `anthias-server` has no host port.

- **Port 443** serves the web interface and the API over HTTPS.
- **Port 80** redirects the web interface to HTTPS. For one more
  release it still answers `/api/*` over plain HTTP, so scripts that
  POST to `http://<device>/api/...` keep working while you move them to
  `https://`. A redirect would break them, because clients turn a
  redirected POST into a GET.

Out of the box the web interface uses the screen's own self-signed
certificate, so browsers show a warning. To remove the warning, give the
screen a certificate for a name that points at it:

- **In the web interface:** **Settings → HTTPS certificate → Upload
  certificate**. Choose the certificate, its private key and, if it is
  not already included in the certificate file, the chain. Anthias
  checks them before switching (the key must match, the certificate must
  not be expired, the chain must be in order) and shows the subject,
  names and expiry. **Revert to default** goes back to the screen's own
  certificate.
- **From a shell**, which ends in exactly the same state:

```bash
# Bring your own certificate (optionally --chain chain.pem).
$ ./bin/enable_ssl.sh --cert /path/to/cert.pem --key /path/to/key.pem

# Let's Encrypt: the domain must resolve to this screen, and port 80 or
# 443 must be reachable from the internet.
$ ./bin/enable_ssl.sh --domain example.com --email <your-email>
$ ./bin/enable_ssl.sh --domain example.com --staging   # ACME staging

# Back to the screen's own certificate.
$ ./bin/disable_ssl.sh
```

The screen's own certificate also identifies it to other Anthias
screens, which recognize it by its fingerprint (shown under **Settings →
HTTPS certificate → Screen identity**). Uploading a web certificate never
changes it. It lives in `~/.anthias/ssl/node/` and is left out of
backups unless you turn on **Include this device's keys** (which
encrypts it with a password you choose), so restoring one screen's
ordinary backup onto another does not give both the same identity.

Upgrading from an older `bin/enable_ssl.sh` setup is automatic: a
bring-your-own certificate becomes the uploaded web certificate, a
`--domain` setup keeps Let's Encrypt for the same domain, and the old
local-CA mode is replaced by the screen's own certificate.

### 2. Trusting a custom CA for outbound requests

If Anthias needs to fetch assets from an internal HTTPS server signed by
a private CA, install the CA into the `anthias-server` and
`anthias-viewer` trust stores:

> **Warning**
>
> This section only works for devices running Raspberry Pi OS Lite.
> 
> ```bash
> $ cd $HOME/anthias
> $ ./bin/add_certificate.sh /path/to/certificate.crt
> ```

For more details, see this [guide to generating self-signed certificates with OpenSSL](https://devopscube.com/create-self-signed-certificates-openssl/).
