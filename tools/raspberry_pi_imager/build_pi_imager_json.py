#!/usr/bin/env python3

import json
import os
import re
from typing import Any

import requests

BASE_URL = 'https://api.github.com/repos/Screenly/Anthias'
GITHUB_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}
# Wide enough to absorb a slow GitHub response without hanging the
# website-deploy job indefinitely. The job runs on every push to master
# and CI's overall budget is in the minutes, not hours.
HTTP_TIMEOUT = 30
SUPPORTED_BOARDS = {'pi2', 'pi3', 'pi4-64', 'pi5'}
MAINTENANCE_BOARDS = {'pi2', 'pi3'}
MAINTENANCE_SUFFIX = (
    ' [Maintenance mode - consider upgrading to Pi 4 or later]'
)

REQUIRED_FIELDS = {
    'name',
    'description',
    'icon',
    'website',
    'extract_size',
    'extract_sha256',
    'image_download_size',
    'image_download_sha256',
    'release_date',
    'url',
}


def get_github_headers() -> dict[str, str]:
    headers = dict(GITHUB_HEADERS)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def get_latest_tag() -> str:
    response = requests.get(
        '{}/releases/latest'.format(BASE_URL),
        headers=get_github_headers(),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    return str(response.json()['tag_name'])


def get_board_from_url(url: str) -> str | None:
    match = re.search(r'-(pi\d(?:-\d+)?)\.img\.zst$', url)
    return match.group(1) if match else None


def get_asset_list(release_tag: str) -> list[str]:
    asset_urls = []
    response = requests.get(
        '{}/releases/tags/{}'.format(BASE_URL, release_tag),
        headers=get_github_headers(),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    for url in response.json()['assets']:
        download_url = url['browser_download_url']
        if not download_url.endswith('.zst'):
            continue
        board = get_board_from_url(download_url)
        if board and board in SUPPORTED_BOARDS:
            asset_urls.append(download_url)

    return asset_urls


def retrieve_and_patch_json(url: str) -> dict[str, Any]:
    response = requests.get(
        url.replace('.img.zst', '.json'),
        headers=get_github_headers(),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    image_json: dict[str, Any] = response.json()

    image_json['url'] = url
    image_json['extract_size'] = int(image_json['extract_size'])
    image_json['image_download_size'] = int(image_json['image_download_size'])

    board = get_board_from_url(url)
    if board and board in MAINTENANCE_BOARDS:
        image_json['description'] += MAINTENANCE_SUFFIX

    return image_json


def build_imager_json() -> dict[str, list[dict[str, Any]]]:
    latest_release = get_latest_tag()
    release_assets = get_asset_list(latest_release)
    pi_imager_json: dict[str, list[dict[str, Any]]] = {'os_list': []}

    for url in release_assets:
        pi_imager_json['os_list'].append(retrieve_and_patch_json(url))

    return pi_imager_json


def main() -> None:
    print(json.dumps(build_imager_json()))


if __name__ == '__main__':
    main()
