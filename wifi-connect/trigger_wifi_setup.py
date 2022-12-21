import zmq
from time import sleep

def main():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind('tcp://0.0.0.0:10001')
    sleep(1)

    socket.send_string('viewer wifi')


if __name__ == '__main__':
    main()
