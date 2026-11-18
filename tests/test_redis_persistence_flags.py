"""A full card must not stop Redis from taking writes.

Redis defaults ``stop-writes-on-bgsave-error`` to ``yes``: after one
failed background save it rejects every write command with ``MISCONF``
until a save succeeds. On a player whose card has filled up, that turns
a local disk problem into a whole-device outage -- the celery worker
exits on its first broker PING ("Unrecoverable error"), and the viewer
can no longer publish the display resolution or drive the command bus,
so the screen stops following the playlist. One pi3 in the field filed
450 of the viewer's half in a single day.

The ``redis-cli ping`` healthcheck in the compose templates cannot
catch this, which is why it needs a test here instead: ``PING`` is a
read, so it keeps answering while every write fails, and the service
stays `healthy` throughout.

These assertions are over the shipped Dockerfile rather than a running
Redis because the flags are declared there, in one CMD that both the
compose and the Balena deployment inherit from the built image.
"""

import os
import re

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)

DOCKERFILE = os.path.join(REPO_ROOT, 'docker', 'Dockerfile.redis.j2')


def _server_argv() -> list[str]:
    """The ``redis-server`` arguments from the Dockerfile's CMD.

    Read out of the template rather than a render: the CMD carries no
    Jinja, so parsing the source keeps this test free of the image
    builder's context. Line continuations are folded first, then every
    double-quoted element is taken in order.
    """
    with open(DOCKERFILE) as f:
        source = f.read()

    body = re.search(
        r'^CMD \[(.*?)\]$',
        source.replace('\\\n', ' '),
        re.MULTILINE | re.DOTALL,
    )
    assert body is not None, f'no CMD array found in {DOCKERFILE}'
    return re.findall(r'"([^"]*)"', body.group(1))


def _flag_value(argv: list[str], flag: str) -> str | None:
    """The value following ``flag``, or None when it is absent."""
    for index, argument in enumerate(argv):
        if argument == flag:
            return argv[index + 1] if index + 1 < len(argv) else None
    return None


def test_a_failed_snapshot_does_not_disable_writes() -> None:
    argv = _server_argv()

    assert _flag_value(argv, '--stop-writes-on-bgsave-error') == 'no', (
        'Redis must keep accepting writes when a background save fails. '
        "With the default ('yes'), a full card makes every write return "
        'MISCONF, which stops the celery worker from starting and the '
        'viewer from reaching the command bus.'
    )


def test_durability_still_rides_on_the_aof() -> None:
    """The flag above is only safe while the AOF is what persists.

    ``stop-writes-on-bgsave-error no`` trades the RDB snapshot for
    availability. That is the right trade only because the device_id
    this image exists to preserve is written through the append-only
    file, which this flag does not affect, onto the mounted volume.
    Drop either and the trade stops holding.
    """
    argv = _server_argv()

    assert argv[0] == 'redis-server'
    assert _flag_value(argv, '--appendonly') == 'yes'
    assert _flag_value(argv, '--dir') == '/var/lib/redis'
