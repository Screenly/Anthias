# Migrating assets from Anthias to Screenly

> [!NOTE]
> This feature is only available in devices running Raspberry Pi OS at the moment.

To get started, SSH to your Raspberry Pi running Anthias. For instance:

```bash
$ ssh pi@raspberrypi
```

Go to the project root directory and install the dependencies required by
the assets migration script using [uv](https://docs.astral.sh/uv/):

```bash
$ cd ~/screenly
$ uv sync --group local
```

Before running the script, you should prepare the following:
* Your Screenly API key
* Anthias username and password, if your device has basic authentication enabled

Run the assets migration script. Follow through the instructions & prompts carefully.

```bash
$ uv run python tools/migrate_assets_to_screenly.py
```