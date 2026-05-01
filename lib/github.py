import logging
import os

from requests import exceptions
from requests import get as requests_get
from requests import head as requests_head

from lib.diagnostics import get_git_hash, get_git_short_hash
from lib.utils import connect_to_redis

r = connect_to_redis()

# Availability and HEAD commit of the remote branch to be checked
# every 24 hours.
REMOTE_BRANCH_STATUS_TTL = 60 * 60 * 24

# Suspend all external requests if we enconter an error other than
# a ConnectionError for 5 minutes.
ERROR_BACKOFF_TTL = 60 * 5

# Availability of the cached published-image-match check.
PUBLISHED_IMAGE_MATCH_TTL = 10 * 60

# Shorter TTL for the "unknown" verdict (per-commit tag missing on
# GHCR). Devices on a not-yet-published commit shouldn't hammer GHCR
# with token + 2xHEAD on every is_up_to_date() call, but we also want
# to pick up a fresh publish quickly once CI finishes.
PUBLISHED_IMAGE_UNKNOWN_TTL = 60

GHCR_IMAGE_REPO = 'screenly/anthias-server'
GHCR_MANIFEST_ACCEPT = ','.join(
    [
        'application/vnd.docker.distribution.manifest.v2+json',
        'application/vnd.docker.distribution.manifest.list.v2+json',
        'application/vnd.oci.image.manifest.v1+json',
        'application/vnd.oci.image.index.v1+json',
    ]
)

DEFAULT_REQUESTS_TIMEOUT = 1  # in seconds


def handle_github_error(
    exc: exceptions.RequestException,
    action: str,
) -> None:
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


def remote_branch_available(branch: str | None) -> bool | None:
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
        return bool(remote_branch_cache == '1')

    # Use the direct branch endpoint (200 = exists, 404 = missing) so we
    # don't silently fail once the repo passes 30 branches: GitHub's
    # /branches list is alphabetically paginated and dropped `master` off
    # the first page once auto-generated branches piled up.
    try:
        resp = requests_get(
            f'https://api.github.com/repos/screenly/anthias/branches/{branch}',
            timeout=DEFAULT_REQUESTS_TIMEOUT,
        )
    except exceptions.RequestException as exc:
        handle_github_error(exc, 'remote branch availability')
        return None

    if resp.status_code == 404:
        r.set('remote-branch-available', '0')
        r.expire('remote-branch-available', REMOTE_BRANCH_STATUS_TTL)
        return False

    try:
        resp.raise_for_status()
    except exceptions.RequestException as exc:
        handle_github_error(exc, 'remote branch availability')
        return None

    r.set('remote-branch-available', '1')
    r.expire('remote-branch-available', REMOTE_BRANCH_STATUS_TTL)
    return True


def fetch_remote_hash() -> tuple[str | None, bool]:
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


def _set_ghcr_error_backoff() -> None:
    r.set('ghcr-api-error', '1')
    r.expire('ghcr-api-error', ERROR_BACKOFF_TTL)


def _get_ghcr_anonymous_token() -> str | None:
    try:
        resp = requests_get(
            'https://ghcr.io/token',
            params={
                'service': 'ghcr.io',
                'scope': f'repository:{GHCR_IMAGE_REPO}:pull',
            },
            timeout=DEFAULT_REQUESTS_TIMEOUT,
        )
        resp.raise_for_status()
    except exceptions.RequestException as exc:
        logging.debug('Failed to fetch GHCR anonymous token: %s', exc)
        _set_ghcr_error_backoff()
        return None
    try:
        token = resp.json().get('token')
    except ValueError:
        _set_ghcr_error_backoff()
        return None
    if not isinstance(token, str):
        _set_ghcr_error_backoff()
        return None
    return token


def _get_ghcr_manifest_digest(tag: str, token: str) -> str | None:
    # HEAD: GHCR returns Docker-Content-Digest in the response headers, so
    # there's no reason to download the manifest body for the match check.
    #
    # 404 is a clean "tag missing" miss — for the per-commit tag this just
    # means the build hasn't been published yet (or registry retention
    # dropped it), so we don't trigger the backoff. Any other failure
    # (network error, 5xx, 429) is transient and *does* trigger the
    # backoff so is_up_to_date() doesn't keep retrying every page load.
    try:
        resp = requests_head(
            f'https://ghcr.io/v2/{GHCR_IMAGE_REPO}/manifests/{tag}',
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': GHCR_MANIFEST_ACCEPT,
            },
            timeout=DEFAULT_REQUESTS_TIMEOUT,
        )
    except exceptions.RequestException as exc:
        logging.debug('Failed to fetch GHCR manifest for %s: %s', tag, exc)
        _set_ghcr_error_backoff()
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        _set_ghcr_error_backoff()
        return None
    # GHCR normally returns Docker-Content-Digest on 200. A missing or
    # empty header is unexpected (header-name change, edge proxy, etc.) —
    # treat it like the other non-404 failures so callers back off
    # instead of caching it as a clean miss.
    digest = resp.headers.get('Docker-Content-Digest')
    if not isinstance(digest, str) or not digest:
        logging.debug(
            'GHCR manifest response for %s missing Docker-Content-Digest',
            tag,
        )
        _set_ghcr_error_backoff()
        return None
    return digest


def is_running_latest_published_image(
    short_hash: str | None, device_type: str | None
) -> bool | None:
    """
    Return True if the device's installed `<short_hash>-<device_type>`
    image manifest matches the floating `latest-<device_type>` manifest
    on GHCR, False if it differs, or None if the lookup fails.

    This is the OR clause that clears the "Update Available" banner
    once a new image has been published even when GitHub master HEAD
    has moved past the published Docker tag (forum 6079, 6144, 6537).
    Anthias publishes to ghcr.io now; the previous Docker Hub tag-list
    path was retired with the registry move.
    """
    if not device_type or not short_hash:
        return None

    # Scope the cache key by device_type + short_hash so a verdict cached
    # for a previous build can't be served for up to PUBLISHED_IMAGE_MATCH_TTL
    # after an upgrade/downgrade. Without this scoping, the banner state can
    # be wrong for ~10 minutes after a deploy.
    cache_key = f'latest-published-image-match:{device_type}:{short_hash}'
    cached = r.get(cache_key)
    if cached is not None:
        if cached == '1':
            return True
        if cached == '0':
            return False
        # '?' (or anything else) is the cached "unknown" sentinel —
        # the per-commit tag was 404 last time we asked. Fall through
        # to None without re-fetching until the short TTL expires.
        return None

    # Backoff after a transient GHCR failure so is_up_to_date() (called on
    # most UI/API requests) doesn't hammer ghcr.io with token + manifest
    # fetches on every page load while the registry is unreachable. The
    # helpers below set this themselves on non-404 failures, so all three
    # call sites share the same throttling — including the per-commit
    # HEAD, which previously fell through without any backoff and could
    # be retried every request on a 5xx/429.
    if r.get('ghcr-api-error') is not None:
        return None

    token = _get_ghcr_anonymous_token()
    if not token:
        return None

    latest_digest = _get_ghcr_manifest_digest(f'latest-{device_type}', token)
    if not latest_digest:
        return None

    current_digest = _get_ghcr_manifest_digest(
        f'{short_hash}-{device_type}', token
    )
    # Tag missing (404) means this device's commit was never published
    # (local build, ahead of CI, or registry retention dropped it). The
    # helper distinguishes 404 from real failures and only the latter
    # triggers the backoff. Cache the "unknown" verdict with a short
    # TTL so the next is_up_to_date() call doesn't redo token + 2xHEAD
    # for a tag we just confirmed is missing — without overshooting so
    # far that a fresh publish takes 10 min to surface.
    if not current_digest:
        r.set(cache_key, '?')
        r.expire(cache_key, PUBLISHED_IMAGE_UNKNOWN_TTL)
        return None

    matches = latest_digest == current_digest
    r.set(cache_key, '1' if matches else '0')
    r.expire(cache_key, PUBLISHED_IMAGE_MATCH_TTL)
    return matches


def is_up_to_date() -> bool:
    """
    Primitive update check. Checks local hash against GitHub hash for branch.
    Returns True if the player is up to date.
    """

    latest_sha, _ = fetch_remote_hash()
    git_hash = get_git_hash()
    git_short_hash = get_git_short_hash()

    if not latest_sha:
        logging.error('Unable to get latest version from GitHub')
        return True

    if latest_sha == git_hash:
        return True

    device_type = os.getenv('DEVICE_TYPE')
    return (
        is_running_latest_published_image(git_short_hash, device_type) is True
    )
