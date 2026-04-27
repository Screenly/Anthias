from threading import Thread
from typing import Any, Callable

from settings import VIEWER_CHANNEL


class ViewerSubscriber(Thread):
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
        pubsub = self.redis_connection.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(VIEWER_CHANNEL)

        # Signal readiness only after subscribe() returns so publishers
        # (notably the host-side wifi-connect script) can wait before sending.
        self.redis_connection.set('viewer-subscriber-ready', int(True))

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
