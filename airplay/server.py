# -*- coding: utf-8 -*-
"""
AirPlay server wrapper that monitors uxplay and publishes session events via ZMQ.
"""

import logging
import os
import re
import signal
import subprocess
import sys
import threading
from time import sleep

import zmq

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('airplay')

# AirPlay session states
STATE_IDLE = 'idle'
STATE_CONNECTED = 'connected'
STATE_STREAMING = 'streaming'


class AirPlayServer:
    """
    Wrapper around uxplay that monitors its output and publishes
    session state changes via ZMQ.
    """

    def __init__(self):
        self.device_name = os.getenv('AIRPLAY_NAME', 'Checkin Cast')
        self.zmq_server_url = os.getenv(
            'ZMQ_SERVER_URL', 'tcp://anthias-server:10001'
        )
        self.audio_output = os.getenv('AUDIO_OUTPUT', 'hdmi')
        self.resolution = os.getenv('AIRPLAY_RESOLUTION', '1920x1080')
        self.framerate = os.getenv('AIRPLAY_FRAMERATE', '30')

        self.process = None
        self.state = STATE_IDLE
        self.running = False
        self.client_name = None

        # ZMQ publisher for session events
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect(self.zmq_server_url.replace(':10001', ':10002'))
        sleep(0.5)  # Allow ZMQ to establish connection

        # Also create a push socket for direct viewer communication
        self.push_socket = self.context.socket(zmq.PUSH)
        self.push_socket.setsockopt(zmq.LINGER, 0)
        self.push_socket.connect('tcp://anthias-server:5559')
        sleep(0.5)

    def _build_command(self):
        """Build the uxplay command with appropriate arguments."""
        width, height = self.resolution.split('x')

        cmd = [
            'uxplay',
            '-n', self.device_name,
            '-s', f'{width}x{height}',
            '-fps', self.framerate,
            '-vs', 'fbdevsink',  # Output to framebuffer
            '-vd', '1',  # Vsync
        ]

        # Audio output configuration
        if self.audio_output == 'hdmi':
            cmd.extend(['-as', 'alsasink device=hw:0,0'])
        elif self.audio_output == 'headphones':
            cmd.extend(['-as', 'alsasink device=hw:1,0'])
        else:
            cmd.extend(['-as', 'alsasink'])

        return cmd

    def _publish_state(self, state, client_name=None):
        """Publish state change via ZMQ."""
        self.state = state
        self.client_name = client_name

        message = {
            'type': 'airplay_state',
            'state': state,
            'client_name': client_name,
        }

        try:
            # Publish to subscriber (for websocket server)
            self.publisher.send_json(message)
            logger.info(f'Published state: {state}, client: {client_name}')

            # Also push directly for viewer
            self.push_socket.send_json(message, flags=zmq.NOBLOCK)
        except zmq.ZMQError as e:
            logger.error(f'Failed to publish state: {e}')

    def _monitor_output(self):
        """Monitor uxplay stdout/stderr for session events."""
        # Patterns to detect session state
        connect_pattern = re.compile(r'Connection from .* \((.+)\)')
        stream_start_pattern = re.compile(r'Starting video stream')
        stream_stop_pattern = re.compile(r'Video stream stopped|Connection closed')

        while self.running and self.process:
            line = self.process.stderr.readline()
            if not line:
                if self.process.poll() is not None:
                    break
                continue

            line = line.decode('utf-8', errors='replace').strip()
            logger.debug(f'uxplay: {line}')

            # Check for connection
            match = connect_pattern.search(line)
            if match:
                client = match.group(1)
                self._publish_state(STATE_CONNECTED, client)
                continue

            # Check for stream start
            if stream_start_pattern.search(line):
                self._publish_state(STATE_STREAMING, self.client_name)
                continue

            # Check for stream stop / disconnect
            if stream_stop_pattern.search(line):
                self._publish_state(STATE_IDLE, None)

    def start(self):
        """Start the AirPlay server."""
        if self.running:
            logger.warning('AirPlay server already running')
            return

        logger.info(f'Starting AirPlay server as "{self.device_name}"')
        self.running = True

        cmd = self._build_command()
        logger.info(f'Command: {" ".join(cmd)}')

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )

            # Start output monitor thread
            monitor_thread = threading.Thread(
                target=self._monitor_output,
                daemon=True
            )
            monitor_thread.start()

            logger.info(f'AirPlay server started (PID: {self.process.pid})')
            self._publish_state(STATE_IDLE)

            # Wait for process to complete
            self.process.wait()

        except Exception as e:
            logger.error(f'Failed to start AirPlay server: {e}')
            self.running = False
            raise
        finally:
            self.running = False
            self._publish_state(STATE_IDLE)

    def stop(self):
        """Stop the AirPlay server."""
        if not self.running or not self.process:
            return

        logger.info('Stopping AirPlay server')
        self.running = False

        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

        self._publish_state(STATE_IDLE)
        logger.info('AirPlay server stopped')

    def cleanup(self):
        """Clean up ZMQ resources."""
        self.stop()
        self.publisher.close()
        self.push_socket.close()
        self.context.term()


def main():
    """Main entry point for the AirPlay server."""
    server = AirPlayServer()

    def signal_handler(signum, frame):
        logger.info(f'Received signal {signum}, shutting down...')
        server.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    while True:
        try:
            server.start()
        except Exception as e:
            logger.error(f'AirPlay server error: {e}')
            sleep(5)  # Wait before retry


if __name__ == '__main__':
    main()
