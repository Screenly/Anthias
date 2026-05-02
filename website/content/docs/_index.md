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

Anthias makes use of Docker for containerization. To get the logs from the
containers, you can either make use of the `docker logs` command or you can
use the `docker compose logs` command.

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

Check out [this section](/docs/developer-documentation/#understanding-the-components-that-make-up-anthias) of the Developer documentation page for the list of available services.

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

To get started, open your browser and go to `http://<ip-address>/api/docs/` (or `http://localhost:8000/api/docs/`
if you're in development mode). You should see the API docs for the endpoints.

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
