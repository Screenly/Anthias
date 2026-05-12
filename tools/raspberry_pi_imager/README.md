# Raspberry Pi Imager JSON Generator

This tool generates the JSON file used by [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to list Anthias disk images. The output is deployed to `anthias.screenly.io/rpi-imager.json` via the website CI workflow.

## Supported Boards

- **pi2** (maintenance mode)
- **pi3** (maintenance mode)
- **pi4-64**
- **pi5**

Pi 1 and Pi Zero are no longer supported.

## Local Development

```bash
pip install requests
python raspberry_pi_imager/bin/build-pi-imager-json.py
```

## How it Works

1. Fetches the latest release from GitHub
2. Filters `.zst` assets to only include supported boards
3. For each matching asset, fetches the corresponding `.json` metadata
4. Patches URLs and file sizes, appends maintenance mode notice for pi2/pi3
5. Outputs a JSON file compatible with Raspberry Pi Imager
