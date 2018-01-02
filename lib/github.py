from requests import get as requests_get
import logging


def branch_exist(branch='master'):
    resp = requests_get(
        'https://api.github.com/repos/screenly/screenly-ose/branches',
        headers={
            'Accept': 'application/vnd.github.loki-preview+json',
        },
    )

    if not resp.ok:
        logging.error('Invalid response from Github: {}'.format(resp.content))
        return

    for github_branch in resp.json():
        if github_branch['name'] == branch:
            return True
    return False


def fetch_hash(branch):
    resp = requests_get(
        'https://api.github.com/repos/screenly/screenly-ose/git/refs/heads/{}'.format(branch)
    )

    if not resp.ok:
        logging.error('Invalid response from github: {}'.format(resp.content))
        return False

    logging.debug('Got response from Github: {}'.format(resp.status_code))
    latest_sha = resp.json()['object']['sha']
    return latest_sha
