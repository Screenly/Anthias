import requests
import json

BASE_URL = "https://api.github.com/repos/Screenly/Anthias"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}


def get_latest_tag():
    response = requests.get(
        "{}/releases/latest".format(BASE_URL),
        headers=GITHUB_HEADERS
    )

    return response.json()['tag_name']


def get_asset_list(release_tag):
    asset_urls = []
    response = requests.get(
        "{}/releases/tags/{}".format(BASE_URL,release_tag),
        headers=GITHUB_HEADERS
    )

    for url in response.json()['assets']:
        if url['browser_download_url'].endswith('.zip'):
            asset_urls.append(url['browser_download_url'])

    return asset_urls


def build_json_structure(url, version):
    """
    {
      "name": "Screenly Player (RPi 3B/3B+)",
      "description": "Player for Screenly digital signage service.",
      "url": "https://disk-images.screenlyapp.com/screenly-v2-pi3-20-2022-12-06.img.zip",
      "icon": "https://disk-images.screenlyapp.com/screenly-logo-32.png",
      "extract_size": 1049624576,
      "extract_sha256": "523c281187fc83694e95fe4f85d1dadc626854fdadebbc9edaa2e5762462836a",
      "image_download_size": 581481378,
      "image_download_sha256": "d38cdb810d4c4fb7c096c98bbb90936025de76939220fa9ee97865ca41948170",
      "release_date": "2022-12-06"
    }
    """

    payload = {}
    payload['name'] = 'Anthias Digital Signage ({})'.format(version)
    payload['description'] = "The world's most popular open source digital signage project"
    payload['icon'] = 'https://raw.githubusercontent.com/Screenly/Anthias/master/static/img/square-dark.svg'
    payload['url'] = url

    print(json.dumps(payload))

def main():
    latest_release = get_latest_tag()
    release_assets = get_asset_list('v0.18.4')

    build_json_structure('google.com', 'pi4')


if __name__ == "__main__":
    main()
