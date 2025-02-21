# Building WebView via Prebuilt Qt

## Overview

This method only works on the following devices:

- Raspberry Pi 4 (64-bit)
- Raspberry Pi 5 (64-bit)

## Prerequisites

> [!NOTE]
> Cross-compilation is not yet supported.
> You need to have the following installed and set up on your Raspberry Pi 4 or Raspberry Pi 5 device:
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

Initialize environment variables:

```bash
$ export GIT_HASH=$(git rev-parse --short HEAD)

$ export COMPOSE_PROFILES=pi5 # For Raspberry Pi 5
$ export COMPOSE_PROFILES=pi4-64 # For Raspberry Pi 4
```

Start the builder container with the following command:

```bash
$ docker compose up -d --build
```

You should now be able to invoke a run executing either of the following commands:

```bash
$ docker compose exec builder-pi5 /webview/build_webview.sh
# or
$ docker compose exec builder-pi4-64 /webview/build_webview.sh
```

```bash
$ docker compose exec builder-pi5 bash
# or
$ docker compose exec builder-pi4-64 bash

# Once you're in the container, run the following command:
$ /scripts/build_webview.sh
```

The resulting files will be placed in `~/tmp/<platform>/build/release`, where `<platform>` is either `pi5` or `pi4-64`.

When you're done, you can stop and remove the container with the following commands:

```bash
$ docker compose down
```
