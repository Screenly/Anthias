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
> daemon — `truncate -s 0` is safe):
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

> **Note**
>
> If TLS is enabled via `bin/enable_ssl.sh`, an additional `anthias-anthias-caddy-1` container runs as a reverse-proxy sidecar.

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
> logged out and back in (and provided the journal is persistent —
> i.e. `/var/log/journal/` exists). On systems where that's set up you
> can drop the `sudo` from the commands above.

Journal retention is controlled by `systemd-journald` (see
`/etc/systemd/journald.conf` — `SystemMaxUse` caps total disk usage,
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

The full endpoint reference is on the [API page](/api/) — endpoints, parameters, and response schemas grouped by tag.

If you'd prefer the live ReDoc-rendered docs straight from your device, open `http://<device-ip>/api/docs/` (or `http://localhost:8000/api/docs/` in development mode).

## TLS / SSL

Anthias supports two independent SSL features:

### 1. Serving HTTPS (Caddy sidecar)

`bin/enable_ssl.sh` writes a `docker-compose.ssl.override.yml` that
adds a `caddy:2-alpine` sidecar in front of `anthias-server`. Caddy
terminates TLS on host ports 80 (redirected to HTTPS) and 443, and
reverse-proxies plain HTTP to `anthias-server:8080`. There are three
modes:

```bash
# Default — Caddy issues a cert from its built-in local CA. Good for
# IP-based LAN access; browsers will warn that the CA is untrusted.
$ ./bin/enable_ssl.sh

# Auto Let's Encrypt — needs the domain to resolve to this host and
# port 80 to be reachable from the internet for the HTTP-01 challenge.
$ ./bin/enable_ssl.sh --domain example.com --email you@example.com
$ ./bin/enable_ssl.sh --domain example.com --staging   # ACME staging

# Bring your own certificate.
$ ./bin/enable_ssl.sh --cert /path/to/cert.pem --key /path/to/key.pem

# Turn it back off (Caddy + override removed; cert files are kept).
$ ./bin/disable_ssl.sh
```

When SSL is *not* enabled, no Caddy container is pulled or run — the
default install is unchanged.

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

More details about generating self-signed certificates can be found [here](https://devopscube.com/create-self-signed-certificates-openssl/).
