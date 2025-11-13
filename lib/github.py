from __future__ import unicode_literals

import json
import logging
import os
import random
import string
from builtins import range, str

from requests import exceptions
from requests import get as requests_get
from requests import post as requests_post

from lib.device_helper import parse_cpu_info
from lib.diagnostics import get_git_branch, get_git_hash, get_git_short_hash
from lib.utils import connect_to_redis, is_balena_app, is_ci, is_docker
from settings import settings

r = connect_to_redis()

# Availability and HEAD commit of the remote branch to be checked
# every 24 hours.
REMOTE_BRANCH_STATUS_TTL = 60 * 60 * 24

# Suspend all external requests if we enconter an error other than
# a ConnectionError for 5 minutes.
ERROR_BACKOFF_TTL = 60 * 5

# Availability of the cached Docker Hub hash
DOCKER_HUB_HASH_TTL = 10 * 60

# Google Analytics data
ANALYTICS_MEASURE_ID = 'G-S3VX8HTPK7'
ANALYTICS_API_SECRET = 'G8NcBpRIS9qBsOj3ODK8gw'

DEFAULT_REQUESTS_TIMEOUT = 1  # in seconds


def handle_github_error(exc, action):
    # After failing, dont retry until backoff timer expires
    r.set('github-api-error', action)
    r.expire('github-api-error', ERROR_BACKOFF_TTL)

    # Print a useful error message
    if exc.response:
        errdesc = exc.response.content
    else:
        errdesc = 'no data'

    logging.error(
        '%s fetching %s from GitHub: %s', type(exc).__name__, action, errdesc
    )


def remote_branch_available(branch):
    if not branch:
        logging.error('No branch specified. Exiting.')
        return None

    # Make sure we havent recently failed before allowing fetch
    if r.get('github-api-error') is not None:
        logging.warning('GitHub requests suspended due to prior error')
        return None

    # Check for cached remote branch status
    remote_branch_cache = r.get('remote-branch-available')
    if remote_branch_cache is not None:
        return remote_branch_cache == '1'

    try:
        resp = requests_get(
            'https://api.github.com/repos/screenly/anthias/branches',
            headers={
                'Accept': 'application/vnd.github.loki-preview+json',
            },
            timeout=DEFAULT_REQUESTS_TIMEOUT,
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
        # Ensure the remote branch is available before trying
        # to fetch the HEAD ref.
        if not remote_branch_available(branch):
            logging.error('Remote Git branch not available')
            return None, False
        try:
            resp = requests_get(
                f'https://api.github.com/repos/screenly/anthias/git/refs/heads/{branch}',  # noqa: E501
                timeout=DEFAULT_REQUESTS_TIMEOUT,
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


def get_latest_docker_hub_hash(device_type):
    """
    This function is useful for cases where latest changes pushed does not
    trigger Docker image builds.
    """

    url = 'https://hub.docker.com/v2/namespaces/screenly/repositories/anthias-server/tags'  # noqa: E501

    cached_docker_hub_hash = r.get('latest-docker-hub-hash')

    if cached_docker_hub_hash:
        try:
            response = requests_get(url, timeout=DEFAULT_REQUESTS_TIMEOUT)
            response.raise_for_status()
        except exceptions.RequestException as exc:
            logging.debug('Failed to fetch latest Docker Hub tags: %s', exc)
            return None

        data = response.json()
        results = data['results']

        reduced = [
            result['name'].split('-')[0]
            for result in results
            if not result['name'].startswith('latest-')
            and result['name'].endswith(f'-{device_type}')
        ]

        if len(reduced) == 0:
            logging.warning(
                'No commit hash found for device type: %s', device_type
            )
            return None

        docker_hub_hash = reduced[0]
        r.set('latest-docker-hub-hash', docker_hub_hash)
        r.expire('latest-docker-hub-hash', DOCKER_HUB_HASH_TTL)

        # Results are sorted by date in descending order,
        # so we can just return the first one.
        return reduced[0]

    return cached_docker_hub_hash


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
        device_id = ''.join(
            random.choice(string.ascii_lowercase + string.digits)
            for _ in range(15)
        )
        r.set('device_id', device_id)
    else:
        device_id = get_device_id

    if retrieved_update:
        if not settings['analytics_opt_out'] and not is_ci():
            ga_base_url = 'https://www.google-analytics.com/mp/collect'
            ga_query_params = f'measurement_id={ANALYTICS_MEASURE_ID}&api_secret={ANALYTICS_API_SECRET}'  # noqa: E501
            ga_url = f'{ga_base_url}?{ga_query_params}'
            payload = {
                'client_id': device_id,
                'events': [
                    {
                        'name': 'version',
                        'params': {
                            'Branch': str(git_branch),
                            'Hash': str(git_short_hash),
                            'NOOBS': os.path.isfile('/boot/os_config.json'),
                            'Balena': is_balena_app(),
                            'Docker': is_docker(),
                            'Pi_Version': parse_cpu_info().get(
                                'model', 'Unknown'
                            ),
                        },
                    }
                ],
            }
            headers = {'content-type': 'application/json'}

            try:
                requests_post(
                    ga_url, data=json.dumps(payload), headers=headers
                )
            except exceptions.ConnectionError:
                pass

    device_type = os.getenv('DEVICE_TYPE')
    latest_docker_hub_hash = get_latest_docker_hub_hash(device_type)

    return (latest_sha == git_hash) or (
        latest_docker_hub_hash == git_short_hash
    )
