---
title: "Migrating Assets to Screenly"
description: "Migrate assets from Anthias to Screenly."
slug: "migrate-to-screenly"
aliases:
  - "/docs/migrating-assets-to-screenly/"
---

> **Note**
>
> This feature is only available in devices running Raspberry Pi OS at the moment.

To get started, SSH to your Raspberry Pi running Anthias. For instance:

```bash
$ ssh pi@raspberrypi
```

Go to the project root directory and install the dependencies required by
the assets migration script using [uv](https://docs.astral.sh/uv/):

```bash
$ cd ~/anthias
$ uv sync --group local
```

Before running the script, you should prepare the following:
* Your Screenly API key
* Anthias username and password, if your device has authentication enabled

> **Note**
>
> The script authenticates with the device using HTTP Basic on the
> `/api/v2/...` endpoints. If your device runs Anthias 2826 or later,
> every authenticated request will emit a `DEPRECATED: HTTP Basic
> auth used …` line in the server log — that's expected. The
> Basic-auth path is retained for back-compat and will be replaced
> by a UI-managed personal-token system in a future release.

Run the assets migration script. Follow through the instructions & prompts carefully.

```bash
$ uv run python tools/migrate_assets_to_screenly.py
```
