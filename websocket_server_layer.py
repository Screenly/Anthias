from gevent import pywsgi
from geventwebsocket import WebSocketError
from geventwebsocket.handler import WebSocketHandler
from threading import Thread
import zmq.green as zmq

from settings import settings


class WebSocketTranslator(object):
    def __init__(self, context):
        self.context = context

    def __call__(self, environ, start_response):
        ws = environ['wsgi.websocket']
        socket = self.context.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, "ws_server")
        socket.connect('inproc://queue')
        try:
            while True:
                msg = socket.recv()
                topic, message = msg.split()
                ws.send(message)
        except WebSocketError:
            ws.close()


class ScreenlyServerListener(Thread):
    def __init__(self, context):
        Thread.__init__(self)
        self.context = context

    def run(self):
        socket_incoming = self.context.socket(zmq.SUB)
        socket_outgoing = self.context.socket(zmq.PUB)

        socket_incoming.connect('tcp://127.0.0.1:10001')
        socket_outgoing.bind('inproc://queue')

        socket_incoming.setsockopt(zmq.SUBSCRIBE, "")
        while True:
            msg = socket_incoming.recv()
            socket_outgoing.send(msg)


if __name__ == "__main__":
    context = zmq.Context()
    listener = ScreenlyServerListener(context)
    listener.start()

    port = int(settings['websocket_port'])
    server = pywsgi.WSGIServer(("", port), WebSocketTranslator(context),
                               handler_class=WebSocketHandler)
    server.serve_forever()
