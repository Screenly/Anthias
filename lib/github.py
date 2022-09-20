import os
import logging
import string
import random
import json
from requests import get as requests_get, post as requests_post, exceptions
from lib.utils import is_balena_app, is_docker, is_ci, connect_to_redis
from lib.diagnostics import get_git_branch, get_git_hash, get_git_short_hash
from lib.raspberry_pi_helper import parse_cpu_info, lookup_raspberry_pi_revision
from settings import settings

r = connect_to_redis()

# Availability and HEAD commit of the remote branch to be checked every 24 hours.
REMOTE_BRANCH_STATUS_TTL = (60 * 60 * 24)

# Suspend all external requests if we enconter an error other than a ConnectionError for 5 minutes
ERROR_BACKOFF_TTL = (60 * 5)

# Google Analytics data
ANALYTICS_MEASURE_ID = 'G-S3VX8HTPK7'
ANALYTICS_API_SECRET = 'G8NcBpRIS9qBsOj3ODK8gw'

def handle_github_error(exc, action):
    # After failing, dont retry until backoff timer expires
    r.set('github-api-error', action)
    r.expire('github-api-error', ERROR_BACKOFF_TTL)

    # Print a useful error message
    if exc.response:
        errdesc = exc.response.content
    else:
        errdesc = 'no data'
    logging.error('{} fetching {} from GitHub: {}'.format(type(exc).__name__, action, errdesc))


def remote_branch_available(branch):
    if not branch:
        logging.error('No branch specified. Exiting.')
        return None

    # Make sure we havent recently failed before allowing fetch
    if r.get('github-api-error') is not None:
        logging.warning("GitHub requests suspended due to prior error")
        return None

    # Check for cached remote branch status
    remote_branch_cache = r.get('remote-branch-available')
    if remote_branch_cache is not None:
        return remote_branch_cache == "1"

    try:
        resp = requests_get(
            'https://api.github.com/repos/screenly/screenly-ose/branches',
            headers={
                'Accept': 'application/vnd.github.loki-preview+json',
            },
        )
        resp.raise_for_status()
    except exceptions.RequestException as exc:
        handle_github_error(exc, 'remote branch availability')
        return None

    found = False
    for github_branch in resp.json():
        if github_branch['name'] == branch:
            found = True
            break

    # Cache and return the result
    if found:
        r.set('remote-branch-available', '1')
    else:
        r.set('remote-branch-available', '0')
    r.expire('remote-branch-available', REMOTE_BRANCH_STATUS_TTL)
    return found


def fetch_remote_hash():
    """
    Returns both the hash and if the status was updated
    or not.
    """
    branch = os.getenv('GIT_BRANCH')

    if not branch:
        logging.error('Unable to get local Git branch')
        return None, False

    get_cache = r.get('latest-remote-hash')
    if not get_cache:
        # Ensure the remote branch is available before trying to fetch the HEAD ref
        if not remote_branch_available(branch):
            logging.error('Remote Git branch not available')
            return None, False
        try:
            resp = requests_get(
                'https://api.github.com/repos/screenly/screenly-ose/git/refs/heads/{}'.format(branch)
            )
            resp.raise_for_status()
        except exceptions.RequestException as exc:
            handle_github_error(exc, 'remote branch HEAD')
            return None, False

        logging.debug('Got response from GitHub: {}'.format(resp.status_code))
        latest_sha = resp.json()['object']['sha']
        r.set('latest-remote-hash', latest_sha)

        # Cache the result for the REMOTE_BRANCH_STATUS_TTL
        r.expire('latest-remote-hash', REMOTE_BRANCH_STATUS_TTL)
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
            ga_url = 'https://www.google-analytics.com/mp/collect?measurement_id={}&api_secret={}'.format(
                    ANALYTICS_MEASURE_ID,
                    ANALYTICS_API_SECRET
            )
            payload = {
                'client_id': device_id,
                'events': [{
                    'name': 'version',
                    'params': {
                        'Branch': str(git_branch),
                        'Hash': str(git_short_hash),
                        'NOOBS': os.path.isfile('/boot/os_config.json'),
                        'Balena': is_balena_app(),
                        'Docker': is_docker(),
                        'Pi_Version': lookup_raspberry_pi_revision(
                            parse_cpu_info()['revision'])['model']
                        }
                }]
            }
            headers = {'content-type': 'application/json'}

            try:
                requests_post(
                    ga_url,
                    data=json.dumps(payload),
                    headers=headers
                )
            except exceptions.ConnectionError:
                pass

    return latest_sha == git_hash
