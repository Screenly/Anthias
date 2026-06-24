# Raspberry Pi Imager JSON Generator

This tool generates the JSON file used by [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to list Anthias disk images. The output is deployed to `anthias.screenly.io/rpi-imager.json` via the website CI workflow.

## Supported Boards

- **pi2** (maintenance mode)
- **pi3-64**
- **pi4-64**
- **pi5**

Pi 1 and Pi Zero are no longer supported. The 32-bit armhf `pi3` image
is still built and remains directly downloadable from the release, but
it is not listed in Imager — Pi 3 users are steered to the 64-bit Qt6
`pi3-64` stream.

## Local Development

```bash
pip install requests
python raspberry_pi_imager/bin/build-pi-imager-json.py
```

## How it Works

1. Fetches the latest release from GitHub
2. Filters `.zst` assets to only include supported boards
3. For each matching asset, fetches the corresponding `.json` metadata
4. Patches URLs and file sizes, tags each entry with its hardware
   `devices` (so Imager's device picker doesn't hide it), and appends a
   maintenance mode notice for pi2/pi3
5. Outputs a JSON file compatible with Raspberry Pi Imager
