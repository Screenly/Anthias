# Building WebView for Raspberry Pi 5

## Prerequisites

> [!NOTE]
> At this time, you can only build the WebView from a Raspberry Pi 5 device.
> You need to have the following installed and set up on your Raspberry Pi 5:
> - Docker (arm64)
> - Code editor of your choice (e.g., Visual Studio Code, Neovim, etc.)

## Building the WebView

Clone the repository:

```bash
$ git clone https://github.com/Screenly/Anthias.git
```

Navigate to the `webview` directory:

```bash
$ cd /path/to/Anthias/webview
```

Start the builder container with the following command:

```bash
$ GIT_HASH=$(git rev-parse --short HEAD) \
    docker compose -f docker-compose.pi5.yml up -d --build
```

You should now be able to invoke a run executing either of the following commands:

```bash
$ docker compose -f docker-compose.pi5.yml exec builder /webview/build_pi5.sh
```

```bash
$ docker compose -f docker-compose.pi5.yml exec builder bash

# Once you're in the container, run the following command:
$ /scripts/build_pi5_webview.sh
```

The resulting files will be placed in `~/tmp-pi5/build/release`.

When you're done, you can stop and remove the container with the following commands:

```bash
$ docker compose -f docker-compose.pi5.yml down
```