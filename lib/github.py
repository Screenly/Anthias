import os
import logging
import string
import random
from requests import get as requests_get, exceptions
from lib.utils import is_balena_app, is_docker, is_ci, connect_to_redis
from lib.diagnostics import get_git_branch, get_git_hash, get_git_short_hash
from mixpanel import Mixpanel, MixpanelException
from settings import settings

r = connect_to_redis()


def remote_branch_available(branch):
    if not branch:
        logging.error('No branch specified. Exiting.')
        return None

    try:
        resp = requests_get(
            'https://api.github.com/repos/screenly/screenly-ose/branches',
            headers={
                'Accept': 'application/vnd.github.loki-preview+json',
            },
        )
    except exceptions.ConnectionError:
        logging.error('No internet connection.')
        return None

    if not resp.ok:
        logging.error('Invalid response from GitHub: {}'.format(resp.content))
        return None

    for github_branch in resp.json():
        if github_branch['name'] == branch:
            return True
    return False


def fetch_remote_hash():
    """
    Returns both the hash and if the status was updated
    or not.
    """
    branch = os.getenv('GIT_BRANCH')
    get_cache = r.get('latest-remote-hash')

    if not branch:
        logging.error('Unable to get Git branch')
        return None, False

    if not get_cache:
        resp = requests_get(
            'https://api.github.com/repos/screenly/screenly-ose/git/refs/heads/{}'.format(branch)
        )

        if not resp.ok:
            logging.error('Invalid response from GitHub: {}'.format(resp.content))
            return None, False

        logging.debug('Got response from GitHub: {}'.format(resp.status_code))
        latest_sha = resp.json()['object']['sha']
        r.set('latest-remote-hash', latest_sha)

        # Cache the result for 24 hours
        r.expire('latest-remote-hash', 24 * 60 * 60)
        return latest_sha, True
    return get_cache, False


def is_up_to_date():
    """
    Primitive update check. Checks local hash against GitHub hash for branch.
    Returns True if the player is up to date.
    """

    latest_sha, retrieved_update = fetch_remote_hash()
    git_branch = get_git_branch()
    git_hash = get_git_hash()
    git_short_hash = get_git_short_hash()
    get_device_id = r.get('device_id')

    if not latest_sha:
        logging.error('Unable to get latest version from GitHub')
        return True

    if not get_device_id:
        device_id = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(15))
        r.set('device_id', device_id)
    else:
        device_id = get_device_id

    if retrieved_update:
        if not settings['analytics_opt_out'] and not is_ci():
            mp = Mixpanel('d18d9143e39ffdb2a4ee9dcc5ed16c56')
            try:
                mp.track(device_id, 'Version', {
                    'Branch': str(git_branch),
                    'Hash': str(git_short_hash),
                    'NOOBS': os.path.isfile('/boot/os_config.json'),
                    'Balena': is_balena_app(),
                    'Docker': is_docker()
                })
            except MixpanelException:
                pass
            except AttributeError:
                pass

    return latest_sha == git_hash
