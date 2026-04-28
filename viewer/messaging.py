import logging
from threading import Thread
from time import sleep
from typing import Any, Callable

import redis

from settings import VIEWER_CHANNEL


class ViewerSubscriber(Thread):
    """Background thread that listens for viewer commands on Redis pub/sub.

    On startup, retries the subscribe() call until it succeeds — the redis
    container can take a few seconds longer than the viewer to start
    accepting connections, especially on first boot. After that, if the
    connection drops mid-stream, re-subscribe with backoff instead of
    letting the thread die silently (which would leave the viewer unable
    to receive ``next``/``previous``/``stop``/``setup_wifi``/etc commands
    for the rest of the process lifetime).

    ``viewer-subscriber-ready`` is only set once subscribe() returns
    successfully — the host-side wifi-connect script polls that flag
    before publishing, so flipping it pre-subscribe would race messages.
    """

    INITIAL_RETRY_DELAY_S = 1
    MAX_RETRY_DELAY_S = 30

    def __init__(
        self,
        redis_connection: Any,
        commands: dict[str, Callable[[str | None], Any]],
        topic: str = 'viewer',
    ) -> None:
        Thread.__init__(self)
        self.redis_connection = redis_connection
        self.commands = commands
        self.topic = topic

    def run(self) -> None:
        delay = self.INITIAL_RETRY_DELAY_S
        while True:
            try:
                pubsub = self.redis_connection.pubsub(
                    ignore_subscribe_messages=True
                )
                pubsub.subscribe(VIEWER_CHANNEL)

                # Subscribe succeeded — clear the readiness flag from any
                # prior crash, then signal we're live so publishers can
                # send. Reset the backoff so the next disconnect starts
                # at INITIAL_RETRY_DELAY_S again.
                self.redis_connection.set('viewer-subscriber-ready', int(True))
                delay = self.INITIAL_RETRY_DELAY_S

                self._consume(pubsub)
            except redis.ConnectionError:
                logging.warning(
                    'Lost Redis connection in ViewerSubscriber; '
                    'reconnecting in %ss.',
                    delay,
                )
                # Mark unready while we're disconnected so any
                # readiness-gated publisher waits instead of dropping
                # messages on the floor.
                try:
                    self.redis_connection.set(
                        'viewer-subscriber-ready', int(False)
                    )
                except redis.ConnectionError:
                    pass
                sleep(delay)
                delay = min(delay * 2, self.MAX_RETRY_DELAY_S)

    def _consume(self, pubsub: Any) -> None:
        for raw in pubsub.listen():
            data = raw.get('data')
            if not isinstance(data, str):
                continue

            topic, _, message = data.partition(' ')
            if topic != self.topic or not message:
                continue

            command, _, parameter = message.partition('&')

            handler = self.commands.get(command, self.commands.get('unknown'))
            if handler is not None:
                handler(parameter or None)
