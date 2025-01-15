# Raspberry Pi Imager JSON Generator

This tool generates the JSON file used by Raspberry Pi Imager to list Anthias images.

## Local Development

To run the JSON generator locally:

```bash
# Start the generator
docker-compose -f docker-compose.pi-imager.yml up --build

# The output will be printed to stdout
```

## Project Structure

```
raspberry_pi_imager/
├── bin/
│   └── build-pi-imager-json.py    # Script that generates the JSON
├── docker-compose.pi-imager.yml   # Docker Compose file for local development
├── Dockerfile.rpi-imager          # Dockerfile for the generator
└── README.md                      # This file
```

## How it Works

1. The script fetches the latest release from GitHub
2. For each `.zip` file in the release:
   * Gets the corresponding `.json` metadata file
   * Patches the URLs and file sizes
   * Adds it to the final JSON output
3. Outputs a JSON file compatible with Raspberry Pi Imager

## Example Output

The JSON output is not indented but we indented it here for readability.

```json
{
  "os_list": [
    {
      "name": "Anthias (pi1)",
      "description": "Anthias, formerly known as Screenly OSE, is the most popular open source digital signage project in the world.",
      "icon": "https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg",
      "website": "https://anthias.screenly.io",
      "extract_size": 5951425536,
      "extract_sha256": "a8a1d1efc6c7a5c3ef196b31e9e4be88893328de25e704fb21d2c71f3c150b2c",
      "image_download_size": 1600981967,
      "image_download_sha256": "52837c254b2c77fcdaa9319243c26f883f34da0c3051e34b79cc8aec59680d13",
      "release_date": "2024-12-23",
      "url": "https://github.com/Screenly/Anthias/releases/download/v0.19.4/2024-12-23-raspberry-pi.zip"
    },
    {
      "name": "Anthias (pi2)",
      "description": "Anthias, formerly known as Screenly OSE, is the most popular open source digital signage project in the world.",
      "icon": "https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg",
      "website": "https://anthias.screenly.io",
      "extract_size": 6193785344,
      "extract_sha256": "f87e3dff29bba1f95c0c4a45aaa0ea315f1462cefd124976cdbc3f7056f448b5",
      "image_download_size": 1723178755,
      "image_download_sha256": "f2096f632c7725b95f234f1bcc60736eb730f2da86c5e41f4ee971137e1a20c1",
      "release_date": "2024-12-23",
      "url": "https://github.com/Screenly/Anthias/releases/download/v0.19.4/2024-12-23-raspberry-pi2.zip"
    },
    {
      "name": "Anthias (pi3)",
      "description": "Anthias, formerly known as Screenly OSE, is the most popular open source digital signage project in the world.",
      "icon": "https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg",
      "website": "https://anthias.screenly.io",
      "extract_size": 6186278400,
      "extract_sha256": "a4302cc7f9b74f56c61c88d2ef1f1d5892b5cbacd5251e9d2ba15639daf127da",
      "image_download_size": 1732004530,
      "image_download_sha256": "f7fb3ffe74346838dc42a65776dbd582de5a887339d5379313cd7b7c839a341f",
      "release_date": "2024-12-23",
      "url": "https://github.com/Screenly/Anthias/releases/download/v0.19.4/2024-12-23-raspberrypi3.zip"
    },
    {
      "name": "Anthias (pi4)",
      "description": "Anthias, formerly known as Screenly OSE, is the most popular open source digital signage project in the world.",
      "icon": "https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg",
      "website": "https://anthias.screenly.io",
      "extract_size": 6219971072,
      "extract_sha256": "c11c0904ccbdd8f7e32dd60359dd796d746b59ed9592befdd3e4b165ed1eda9d",
      "image_download_size": 1736755576,
      "image_download_sha256": "6cc555f388f77c2ad07c6c8e614f97eb0ce38847626e22613787a12c2968ac8c",
      "release_date": "2024-12-23",
      "url": "https://github.com/Screenly/Anthias/releases/download/v0.19.4/2024-12-23-raspberrypi4-64.zip"
    },
    {
      "name": "Anthias (pi5)",
      "description": "Anthias, formerly known as Screenly OSE, is the most popular open source digital signage project in the world.",
      "icon": "https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg",
      "website": "https://anthias.screenly.io",
      "extract_size": 7312135168,
      "extract_sha256": "53e36a642edb5bbd0258b4df404f3483b8a4511954a025ecc155d1e248f6f1bf",
      "image_download_size": 1659320107,
      "image_download_sha256": "a144ea0a308618a60e7f60369c445b3d82b0e75b981ab5780ce78ce6b08df6a7",
      "release_date": "2024-12-23",
      "url": "https://github.com/Screenly/Anthias/releases/download/v0.19.4/2024-12-23-raspberrypi5.zip"
    }
  ]
}
```