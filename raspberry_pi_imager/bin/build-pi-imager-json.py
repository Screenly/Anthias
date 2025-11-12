#!/usr/bin/env python3

from __future__ import print_function, unicode_literals

import json

import requests

BASE_URL = 'https://api.github.com/repos/Screenly/Anthias'
GITHUB_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}


def get_latest_tag():
    response = requests.get(
        '{}/releases/latest'.format(BASE_URL), headers=GITHUB_HEADERS
    )

    return response.json()['tag_name']


def get_asset_list(release_tag):
    asset_urls = []
    response = requests.get(
        '{}/releases/tags/{}'.format(BASE_URL, release_tag),
        headers=GITHUB_HEADERS,
    )

    for url in response.json()['assets']:
        download_url = url['browser_download_url']
        if download_url.endswith('.zst'):
            asset_urls.append(download_url)

    return asset_urls


def retrieve_and_patch_json(url):
    image_json = requests.get(
        url.replace('.img.zst', '.json'), headers=GITHUB_HEADERS
    ).json()

    image_json['url'] = url
    image_json['extract_size'] = int(image_json['extract_size'])
    image_json['image_download_size'] = int(image_json['image_download_size'])
    return image_json


def main():
    latest_release = get_latest_tag()
    release_assets = get_asset_list(latest_release)
    pi_imager_json = {'os_list': []}

    for url in release_assets:
        pi_imager_json['os_list'].append(retrieve_and_patch_json(url))

    print(json.dumps(pi_imager_json))


if __name__ == '__main__':
    main()
