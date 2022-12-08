import requests

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

    for files
