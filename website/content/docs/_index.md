---
title: "Documentation"
description: "Anthias documentation: installation, deployment, development, and operations."
---

## How to get logs from Anthias

SSH into your Raspberry Pi. For instance, if you've set `pi` for the username
and `raspberrypi` for the hostname, then run:

```bash
$ ssh pi@raspberrypi
```

Anthias ships its container logs through the host's `systemd-journald`,
so the system handles rotation and retention for you. The four core
services (`anthias-server`, `anthias-viewer`, `anthias-celery`, `redis`)
plus the optional `anthias-caddy` TLS sidecar all use the journald
driver, so they don't write the unbounded `*-json.log` files under
`/var/lib/docker/containers/` that can fill an SD card on long-running
installs. You can read the logs three ways: `docker logs`,
`docker compose logs`, or `journalctl` directly.

> **Note**
>
> Switching the driver only affects future writes. Devices that were
> on the old `json-file` driver will still have the existing log files
> on disk after upgrading. To reclaim that space, truncate the leftover
> files in place (Docker keeps them open, so deleting can confuse the
> daemon, `truncate -s 0` is safe):
>
> ```bash
> $ sudo find /var/lib/docker/containers/ -name "*-json.log" \
>     -exec truncate -s 0 {} +
> ```

### Using `docker logs`

For instance, the command below will show you the logs from the server container:

```bash
$ docker logs -f anthias-anthias-server-1
```

If you'd want to see the logs from other containers, simply replace the name
of the container in the command above. Here's a table of the available containers:

| Container Name | Description |
| -------------- | ----------- |
| `anthias-anthias-server-1` | uvicorn (HTTP, WebSocket at `/ws`, static + media file serving) |
| `anthias-anthias-celery-1` | Celery worker (async tasks) |
| `anthias-anthias-viewer-1` | Viewer service driving the screen |
| `anthias-redis-1` | Redis (Celery broker + Channels layer) |
| `anthias-anthias-caddy-1` | Caddy, the web front end on ports 80 and 443 (TLS, routing) |

### Using `docker compose logs`

> **Important**
>
> Before running the succeeding commands, make sure that you're in the
> `/home/${USER}/anthias` directory:
> 
> ```bash
> $ cd /home/${USER}/anthias # e.g., /home/pi/anthias if the user is `pi`
> ```

If you'd like to see the logs of a specific container or service via Docker Compose,
you can run the following:

```bash
$ docker compose logs -f ${SERVICE_NAME}
# e.g., docker compose logs -f anthias-server
```

Check out [this section](/docs/development/#understanding-the-components-that-make-up-anthias) of the Developer documentation page for the list of available services.

### Using `journalctl`

Each service is tagged in the journal so you can pull logs without
docker. Useful when you want to grep across a long time range or
combine container logs with system logs:

```bash
$ sudo journalctl -f CONTAINER_TAG=anthias-server
$ sudo journalctl --since "1 hour ago" CONTAINER_TAG=anthias-viewer
```

The available tags are `anthias-server`, `anthias-viewer`,
`anthias-celery`, `anthias-redis`, and (when TLS is enabled)
`anthias-caddy`.

> **Note**
>
> The Anthias installer adds your user to the `adm` group, which on
> Debian/Raspberry Pi OS grants read access to the journal once you've
> logged out and back in (and provided the journal is persistent, 
> i.e. `/var/log/journal/` exists). On systems where that's set up you
> can drop the `sudo` from the commands above.

Journal retention is controlled by `systemd-journald` (see
`/etc/systemd/journald.conf`, `SystemMaxUse` caps total disk usage,
defaulting to 10% of the filesystem). If you want to free space
immediately:

```bash
$ sudo journalctl --vacuum-time=2d   # drop entries older than 2 days
$ sudo journalctl --vacuum-size=200M # cap journal at 200 MB
```

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

## Accessing the REST API

The full endpoint reference is on the [API page](/api/): endpoints, parameters, and response schemas grouped by tag.

If you'd prefer the live ReDoc-rendered docs straight from your device, open `http://<device-ip>/api/docs/` (or `http://localhost:8000/api/docs/` in development mode).

### Authenticating with API tokens

When **Authentication** is turned on in Settings, scripts and other devices should use an API token instead of your username and password.

1. Go to **Settings → API tokens**, give the token a name (for example, the machine that will use it), and click **Create token**.
2. Copy the token right away. Anthias stores only a hash of it, so it can't be shown again.
3. Send it in the `Authorization` header:

```bash
$ curl -H 'Authorization: Bearer anthias_…' http://<device-ip>/api/v2/info
```

A token gives full access to the API, the same as the operator account. It can't be used to sign in to the web interface or to create more tokens. To cut off access, click **Revoke** next to the token; anything still using it gets `401 Unauthorized` from then on. Changing your password does not revoke tokens.

Tokens only apply while authentication is on:

* With **Authentication** set to **Disabled**, the API is open to anyone on your network. You can't create tokens in that mode, because they would not protect anything, and a token sent in a request is ignored.
* Tokens you created earlier stay listed (and can still be revoked) but are inactive until you turn authentication back on.

HTTP Basic auth (`curl -u user:pass …`) still works for older clients but is deprecated in favor of tokens.

### Managing several screens from one (multi-screen)

One Anthias screen (the controller) can publish the same playlist to other screens on your network. Joining a screen to a controller works like joining a Proxmox cluster:

1. On the controller, open **Screens** and copy the **Join information**. It starts with `anthias-join:`.
2. On the screen you want to add, turn on **Authentication** in Settings if it isn't on yet. Joining is refused while authentication is disabled, because the API would be open to anyone on the network.
3. Go to **Settings → Multi-screen management**, paste the join information and click **Join controller**. If the controller can't reach the screen at the address it connected from (for example, across a NAT), also enter the screen's IP address or DNS name.

The screen then shows a **Managed by …** banner on every page. Content you change locally is replaced the next time the controller publishes.

**How it stays secure:** every Anthias screen has its own long-lived, self-signed certificate, created on first start. The join information carries the controller's certificate fingerprint, so the screen's very first connection is pinned to it and nobody else can stand in for the controller. The screen sends its own fingerprint and an API token in return; the controller pins that fingerprint for everything it does afterwards. Controller traffic uses a separate port, **8443**, that always presents the screen's own certificate and only serves `/api/v2/`. Replacing the certificate for the web interface doesn't affect pairings. Port 8443 is only open while the screen is managed (or is a controller), and never while authentication is disabled.

The screen keeps only a hash of the controller's token, like any API token. To stop being managed, click **Leave management** in Settings: the token is revoked at once, port 8443 closes, and the controller shows the screen as having left. Revoking the `Controller: …` token under **API tokens** has the same effect. The screen's identity certificate is not part of backups, so restoring a backup on another device doesn't clone a screen's identity.

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
changes it. It lives in `~/.anthias/ssl/node/` and is deliberately left
out of backups, so restoring one screen's backup onto another does not
give both the same identity.

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
