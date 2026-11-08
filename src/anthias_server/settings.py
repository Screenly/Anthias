import configparser
import json
import logging
from collections import UserDict
from os import getenv, path
from typing import TYPE_CHECKING, Any, ClassVar

from anthias_common.errors import ReplyTimeoutError

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

VIEWER_CHANNEL = 'anthias.viewer'
REPLY_KEY_PREFIX = 'anthias.reply.'

#: Socket headroom over a blocking pop's own deadline. The socket read
#: has to outlive the server-side BLPOP timer: if it expires first, the
#: client abandons a pop that was about to come back empty, and "came
#: back empty" is precisely the outcome the caller needs to report. See
#: ``ReplyCollector._blocking_connection``.
REPLY_SOCKET_MARGIN_SECONDS = 5

CONFIG_DIR = '.anthias/'
CONFIG_FILE = 'anthias.conf'
DEFAULTS = {
    'main': {
        'analytics_opt_out': False,
        'assetdir': 'anthias_assets',
        'database': CONFIG_DIR + 'anthias.db',
        'date_format': 'mm/dd/yyyy',
        'splash_logo_url': '/static/img/logo-full-splash.svg',
        # Operator-selected IANA timezone. Empty defers to the resolved
        # default — see resolve_time_zone() in django_project/settings.py
        # for the config -> TZ env -> /etc/timezone -> UTC precedence. Kept
        # userspace on purpose: balenaOS has no host timezone (always
        # UTC), so this is the only way to schedule/display in local
        # time there.
        'timezone': '',
        # Size of each request a large browser upload is split into,
        # for an operator behind a proxy that caps request bodies.
        # Empty defers to the resolved default — see
        # resolve_upload_chunk_size_mb() in django_project/settings.py
        # for the config -> ANTHIAS_UPLOAD_CHUNK_SIZE_MB env -> 16
        # precedence. Lives here so a docker-compose install has one
        # place to set it that survives an upgrade: this file is on the
        # mounted volume, while docker-compose.yml is regenerated from
        # its template on every run.
        'upload_chunk_size_mb': '',
        'use_24_hour_clock': False,
        'use_ssl': False,
        'auth_backend': '',
        'django_secret_key': '',
        # State for the "Star on GitHub / Review on G2" nudge (server-
        # side so it survives a browser/cache clear and stays consistent
        # across every device the operator opens the UI from).
        # ``review_cta_dismissed`` is set once the operator acts on a
        # CTA (starred or reviewed) and permanently silences the nudge;
        # ``review_cta_snooze_until`` is an ISO-8601 timestamp set by
        # "Maybe later" (and the close button) that suppresses the nudge
        # until it passes.
        'review_cta_dismissed': False,
        'review_cta_snooze_until': '',
        # Scheduled display power (see lib/display_power.py). Times are
        # 'HH:MM' in the device's configured timezone; days is a
        # comma-separated list of Python weekday numbers (Monday=0)
        # selecting the days an on-period *begins*. Off by default —
        # a device that has always stayed on must not start switching
        # itself off after an upgrade.
        'display_power_schedule_enabled': False,
        'display_power_on_time': '08:00',
        'display_power_off_time': '18:00',
        'display_power_days': '0,1,2,3,4,5,6',
    },
    'viewer': {
        'audio_output': 'hdmi',
        'debug_logging': False,
        'default_duration': 10,
        'default_streaming_duration': '300',
        'player_name': '',
        'prefer_dark_mode': False,
        'resolution': '1920x1080',
        'screen_rotation': 0,
        'show_splash': True,
        'shuffle_playlist': False,
        'verify_ssl': True,
        'default_assets': False,
    },
}
CONFIGURABLE_SETTINGS = DEFAULTS['viewer'].copy()
CONFIGURABLE_SETTINGS['use_24_hour_clock'] = DEFAULTS['main'][
    'use_24_hour_clock'
]
CONFIGURABLE_SETTINGS['date_format'] = DEFAULTS['main']['date_format']
CONFIGURABLE_SETTINGS['timezone'] = DEFAULTS['main']['timezone']

PORT = int(getenv('PORT', '8080'))
LISTEN = getenv('LISTEN', '127.0.0.1')

# Initiate logging
logging.basicConfig(
    level=logging.INFO, format='%(message)s', datefmt='%a, %d %b %Y %H:%M:%S'
)

# Silence urllib info messages ('Starting new HTTP connection')
# that are triggered by the remote url availability check in view_web
requests_log = logging.getLogger('requests')
requests_log.setLevel(logging.WARNING)

logger.debug('Starting viewer')


class AnthiasSettings(UserDict[str, Any]):
    """Anthias' Settings."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        UserDict.__init__(self, *args, **kwargs)
        home = getenv('HOME')
        if not home:
            # Without HOME, all config/state paths (config file, DB,
            # asset dir) would silently resolve relative to the cwd.
            # Fail loudly instead of writing to unexpected locations.
            raise OSError(
                'HOME environment variable must be set for AnthiasSettings.'
            )
        self.home = home
        self.conf_file = self.get_configfile()

        if not path.isfile(self.conf_file):
            logger.error(
                'Config-file %s missing. Using defaults.', self.conf_file
            )
            self.use_defaults()
            self.save()
        else:
            self.load()

    def _get(
        self,
        config: configparser.ConfigParser,
        section: str,
        field: str,
        default: Any,
    ) -> None:
        try:
            if isinstance(default, bool):
                self[field] = config.getboolean(section, field)
            elif isinstance(default, int):
                self[field] = config.getint(section, field)
            else:
                self[field] = config.get(section, field)
        except configparser.Error as e:
            logger.debug(
                "Could not parse setting '%s.%s': %s. "
                "Using default value: '%s'.",
                section,
                field,
                str(e),
                default,
            )
            self[field] = default
        if field in ['database', 'assetdir']:
            self[field] = str(path.join(self.home, self[field]))

    def _set(
        self,
        config: configparser.ConfigParser,
        section: str,
        field: str,
        default: Any,
    ) -> None:
        if isinstance(default, bool):
            config.set(
                section, field, self.get(field, default) and 'on' or 'off'
            )
        else:
            config.set(section, field, str(self.get(field, default)))

    def load(self) -> None:
        """Loads the latest settings from anthias.conf into memory."""
        logger.debug('Reading config-file...')
        config = configparser.ConfigParser()
        config.read(self.conf_file)

        for section, defaults in list(DEFAULTS.items()):
            for field, default in list(defaults.items()):
                self._get(config, section, field, default)

    def use_defaults(self) -> None:
        for defaults in list(DEFAULTS.items()):
            for field, default in list(defaults[1].items()):
                self[field] = default

    def save(self) -> None:
        # Write new settings to disk.
        config = configparser.ConfigParser()
        for section, defaults in list(DEFAULTS.items()):
            config.add_section(section)
            for field, default in list(defaults.items()):
                self._set(config, section, field, default)
        with open(self.conf_file, 'w') as f:
            config.write(f)
        self.load()

    def get_configdir(self) -> str:
        return path.join(self.home, CONFIG_DIR)

    def get_configfile(self) -> str:
        return path.join(self.home, CONFIG_DIR, CONFIG_FILE)


settings = AnthiasSettings()


class ViewerPublisher:
    INSTANCE: ClassVar['ViewerPublisher | None'] = None

    def __init__(self) -> None:
        if self.INSTANCE is not None:
            raise ValueError('An instance already exists!')

        from anthias_common.utils import connect_to_redis

        self._redis: redis.Redis = connect_to_redis()

    @classmethod
    def get_instance(cls) -> 'ViewerPublisher':
        if cls.INSTANCE is None:
            cls.INSTANCE = ViewerPublisher()
        return cls.INSTANCE

    def send_to_viewer(self, msg: str) -> None:
        self._redis.publish(VIEWER_CHANNEL, f'viewer {msg}')


class ReplySender:
    """Push a JSON reply onto a per-correlation-ID list. Used by the viewer
    to answer request-reply commands like ``current_asset_id``.

    The list expires after 30s so unread replies (e.g. server timed out
    before the viewer answered) don't accumulate in Redis.

    Takes the caller's redis connection so we don't open a fresh
    connection (and connection pool) per reply — the viewer reuses its
    process-wide ``r`` here.
    """

    def __init__(self, redis_connection: 'redis.Redis') -> None:
        self._redis = redis_connection

    def send(self, correlation_id: str, msg: Any) -> None:
        key = f'{REPLY_KEY_PREFIX}{correlation_id}'
        self._redis.rpush(key, json.dumps(msg))
        self._redis.expire(key, 30)


class ReplyCollector:
    INSTANCE: ClassVar['ReplyCollector | None'] = None

    def __init__(self) -> None:
        if self.INSTANCE is not None:
            raise ValueError('An instance already exists!')

        from anthias_common.utils import connect_to_redis

        self._redis: redis.Redis = connect_to_redis()
        self._blocking_redis: dict[int, redis.Redis] = {}

    @classmethod
    def get_instance(cls) -> 'ReplyCollector':
        if cls.INSTANCE is None:
            cls.INSTANCE = ReplyCollector()
        return cls.INSTANCE

    def _blocking_connection(self, timeout_seconds: int) -> 'redis.Redis':
        """A client whose socket read can actually carry this BLPOP.

        ``connect_to_redis()`` takes redis-py's defaults, and for a
        blocking pop those defaults are wrong in a way that hides
        itself: ``socket_timeout=5`` with ``Retry(retries=10)``. The
        socket read gives up five seconds into a longer pop, the retry
        ladder silently re-issues it ten more times, and about a minute
        later the call raises ``redis.TimeoutError`` instead of
        returning ``None``.

        That is not theoretical. ``lib/cec_client`` sizes its reply
        budget from the viewer's worst-case CEC fan-out
        (``REPLY_TIMEOUT_MS`` = 15s), and a 15s pop against a viewer
        that is alive but slow to answer was measured at **58.0s** —
        long enough to blow ``get_display_power``'s 30s soft limit and
        reach its 60s hard limit, at which point billiard SIGKILLs the
        pool child and celery forks a replacement (Sentry
        ANTHIAS-A / 9 / B / 1Q). The caller's own timeout bounded
        nothing; the retry ladder was the real bound, at roughly four
        times the intended budget.

        Worse, the ladder raises instead of returning ``None``, so the
        ``ReplyTimeoutError`` -> ``ViewerUnavailableError`` path that
        turns a late viewer into a clean "Error" reading was
        unreachable from here. A slow viewer surfaced as a dead celery
        worker.

        This is the same trap ``lib/system_logs._reply_connection``
        already removes for the host-agent pop, and the same fix:
        widen the socket read past the pop's own deadline and turn
        retries off. Retries are off deliberately — a blocking pop is
        not idempotent the way a GET is, and re-issuing it after a
        socket timeout restarts the wait from zero, which is exactly
        how one bounded request grew into a minute-long stall.

        Sized per call and cached per socket timeout rather than fixed
        at construction, so a caller that widens its budget is carried
        automatically instead of silently falling back into the ladder.
        There are two call sites today (2s and 15s), so the cache holds
        two connections.
        """
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        from anthias_common.utils import connect_to_redis

        socket_timeout = timeout_seconds + REPLY_SOCKET_MARGIN_SECONDS
        connection = self._blocking_redis.get(socket_timeout)
        if connection is None:
            connection = connect_to_redis(
                socket_timeout=socket_timeout,
                retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False,
            )
            self._blocking_redis[socket_timeout] = connection
        return connection

    def recv_json(self, correlation_id: str, timeout_ms: int) -> Any:
        key = f'{REPLY_KEY_PREFIX}{correlation_id}'

        # ``timeout_ms <= 0`` is a non-blocking poll — match the old ZMQ
        # collector's contract (zmq.poll(0) returns immediately) instead
        # of blocking BLPOP for a full second. Use LPOP and raise the
        # same timeout error if nothing's queued.
        #
        # Stays on the shared connection: redis-py's defaults are right
        # for a short round trip, which is what this branch is.
        if timeout_ms <= 0:
            result = self._redis.lpop(key)
            if result is None:
                raise ReplyTimeoutError
            return json.loads(result)

        # BLPOP takes whole seconds; round up so the caller never waits
        # less than the requested ms.
        timeout_seconds = (timeout_ms + 999) // 1000
        blpop_result = self._blocking_connection(timeout_seconds).blpop(
            key, timeout=timeout_seconds
        )
        if blpop_result is None:
            raise ReplyTimeoutError
        return json.loads(blpop_result[1])
