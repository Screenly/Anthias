import json
import logging
from threading import Thread

import websockets.sync.client

logger = logging.getLogger(__name__)


class WebSocketSubscriber(Thread):
    def __init__(self, commands: dict, server_url: str) -> None:
        super().__init__()
        self.commands = commands
        self.server_url = server_url

    def run(self) -> None:
        while True:
            try:
                self._connect_and_listen()
            except Exception:
                logger.exception(
                    'WebSocket connection failed, reconnecting...'
                )
                import time

                time.sleep(5)

    def _connect_and_listen(self) -> None:
        with websockets.sync.client.connect(
            self.server_url,
            close_timeout=5,
        ) as ws:
            logger.info('Connected to WebSocket server: %s', self.server_url)
            while True:
                msg = ws.recv()
                try:
                    data = json.loads(msg)
                    command = data.get('command', '')
                    parameter = data.get('data')

                    # Handle command&parameter format
                    parts = command.split('&', 1)
                    cmd = parts[0]
                    if len(parts) > 1:
                        parameter = parts[1]

                    handler = self.commands.get(
                        cmd, self.commands.get('unknown')
                    )
                    handler(parameter)
                except Exception:
                    logger.exception('Error handling message: %s', msg)
