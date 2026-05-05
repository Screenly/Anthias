import logging

from packaging.version import InvalidVersion, Version
from requests import exceptions
from requests import get as requests_get

from anthias_common.utils import connect_to_redis
from anthias_server.lib.diagnostics import get_anthias_release

r = connect_to_redis()

# Cache the latest-release lookup for 24h. Lines up with GitHub's
# release cadence so we don't hammer the API but still surface a new
# release within a day.
LATEST_RELEASE_TTL = 60 * 60 * 24

# Suspend further GitHub API requests for 5 minutes after a non-404
# error (rate limit, 5xx, network blip).
ERROR_BACKOFF_TTL = 60 * 5

# Cache key for the latest release tag (TTL'd).
LATEST_RELEASE_TAG_KEY = 'latest-release-tag'
# Cache-key prefix for the most recent successfully-computed verdict.
# Scoped by local release so an upgrade during a GitHub outage doesn't
# reuse the old version's verdict (the previous "up to date" might no
# longer apply, and vice versa). Written on every fresh comparison and
# read only as the fallback when both the tag cache and a fresh fetch
# fail. No TTL — overwritten on the next successful check.
LAST_VERDICT_KEY_PREFIX = 'is-up-to-date:last-verdict'

DEFAULT_REQUESTS_TIMEOUT = 5  # seconds

GITHUB_RELEASES_LATEST_URL = (
    'https://api.github.com/repos/Screenly/Anthias/releases/latest'
)
GITHUB_API_ACCEPT = 'application/vnd.github+json'


def _set_github_error_backoff(action: str) -> None:
    r.set('github-api-error', action)
    r.expire('github-api-error', ERROR_BACKOFF_TTL)


def handle_github_error(
    exc: exceptions.RequestException,
    action: str,
) -> None:
    _set_github_error_backoff(action)

    if exc.response is not None:
        errdesc = exc.response.content
    else:
        errdesc = 'no data'

    logging.error(
        '%s fetching %s from GitHub: %s', type(exc).__name__, action, errdesc
    )


def _fetch_latest_release_tag() -> str | None:
    """Return the latest release tag from GitHub, hitting the API at
    most once per ``LATEST_RELEASE_TTL`` and short-circuiting while a
    prior error backoff is active. Returns ``None`` on any failure.
    """
    cached = r.get(LATEST_RELEASE_TAG_KEY)
    if cached:
        return cached

    if r.get('github-api-error') is not None:
        logging.warning('GitHub requests suspended due to prior error')
        return None

    try:
        resp = requests_get(
            GITHUB_RELEASES_LATEST_URL,
            headers={'Accept': GITHUB_API_ACCEPT},
            timeout=DEFAULT_REQUESTS_TIMEOUT,
        )
        resp.raise_for_status()
    except exceptions.RequestException as exc:
        handle_github_error(exc, 'latest release')
        return None

    # Trip the same 5-minute backoff for malformed bodies as for
    # transport failures. Without this, a bad JSON body or missing
    # tag_name from a 200 response would re-fire the GitHub call on
    # every page render until upstream fixed the payload.
    try:
        payload = resp.json()
    except ValueError:
        logging.error('Malformed JSON from GitHub /releases/latest')
        _set_github_error_backoff('latest release: malformed JSON')
        return None

    tag = payload.get('tag_name') if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag:
        logging.error('Missing tag_name in /releases/latest response')
        _set_github_error_backoff('latest release: missing tag_name')
        return None

    # Validate parseability *before* caching. Otherwise a bad upstream
    # tag (e.g. a hand-edited release with `nightly`) would be cached
    # for 24h, locking is_up_to_date() into the fallback verdict for
    # the full TTL even after upstream corrects the tag.
    if _parse_version(tag) is None:
        logging.error('Unparseable tag_name from GitHub: %r', tag)
        _set_github_error_backoff('latest release: unparseable tag_name')
        return None

    r.set(LATEST_RELEASE_TAG_KEY, tag)
    r.expire(LATEST_RELEASE_TAG_KEY, LATEST_RELEASE_TTL)
    return tag


def _parse_version(value: str) -> Version | None:
    """Parse a CalVer string, tolerating the conventional leading ``v``
    on git tags (``v2026.5.0``)."""
    cleaned = value[1:] if value[:1] in ('v', 'V') else value
    try:
        return Version(cleaned)
    except InvalidVersion:
        return None


def _verdict_cache_key(local_release: str) -> str:
    return f'{LAST_VERDICT_KEY_PREFIX}:{local_release}'


def _fallback_verdict(local_release: str) -> bool:
    """Return the last verdict cached for this exact local release, or
    ``False`` if there isn't one (don't claim "up to date" when we
    don't actually know — and don't reuse a verdict computed against a
    different installed version)."""
    cached = r.get(_verdict_cache_key(local_release))
    if cached is None:
        return False
    return cached == '1'


def is_up_to_date() -> bool:
    """Return ``True`` if this device is on (or ahead of) the latest
    published Anthias release.

    Compares ``importlib.metadata.version('anthias')`` (CalVer, sourced
    from ``pyproject.toml``) against the ``tag_name`` of GitHub's
    ``/releases/latest`` endpoint. Caches the remote tag for 24h and
    falls back to the last computed verdict if GitHub is unreachable.

    Returns ``True`` (suppressing the "Update available" indicator)
    when the local CalVer can't be parsed, e.g. on dev / branch builds
    detached from a tagged release — there's no useful comparison to
    make and the indicator should not shout at developers.
    """
    local_release = get_anthias_release()
    local_version = _parse_version(local_release) if local_release else None
    if local_version is None:
        return True

    latest_tag = _fetch_latest_release_tag()
    if not latest_tag:
        return _fallback_verdict(local_release)

    latest_version = _parse_version(latest_tag)
    if latest_version is None:
        logging.error('Malformed tag_name from GitHub: %r', latest_tag)
        return _fallback_verdict(local_release)

    verdict = local_version >= latest_version
    r.set(_verdict_cache_key(local_release), '1' if verdict else '0')
    return verdict
