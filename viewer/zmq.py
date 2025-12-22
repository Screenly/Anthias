from builtins import bytes
from threading import Thread

import zmq

ZMQ_HOST_PUB_URL = 'tcp://host.docker.internal:10001'


class ZmqSubscriber(Thread):
    def __init__(
        self,
        redis_connection,
        commands,
        publisher_url,
        topic='viewer',
    ):
        Thread.__init__(self)
        self.context = zmq.Context()
        self.publisher_url = publisher_url
        self.topic = topic
        self.commands = commands
        self.redis_connection = redis_connection

    def run(self):
        socket = self.context.socket(zmq.SUB)
        socket.connect(self.publisher_url)
        socket.setsockopt(zmq.SUBSCRIBE, bytes(self.topic, encoding='utf-8'))

        if self.publisher_url == ZMQ_HOST_PUB_URL:
            self.redis_connection.set('viewer-subscriber-ready', int(True))

        while True:
            msg = socket.recv()
            topic, message = msg.decode('utf-8').split(' ', 1)

            # If the command consists of 2 parts, then the first is the
            # function and the second is the argument.
            parts = message.split('&', 1)
            command = parts[0]
            parameter = parts[1] if len(parts) > 1 else None

            self.commands.get(command, self.commands.get('unknown'))(parameter)
