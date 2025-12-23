# -*- coding: utf-8 -*-
"""
AirPlay session handler for the viewer.

This module subscribes to AirPlay session events and coordinates
with the main viewer loop to pause/resume the playlist.
"""

import json
import logging
from threading import Thread

import zmq

logger = logging.getLogger('viewer.airplay')

# AirPlay session states (match those in airplay/server.py)
STATE_IDLE = 'idle'
STATE_CONNECTED = 'connected'
STATE_STREAMING = 'streaming'


class AirPlayStateManager:
    """
    Singleton manager for AirPlay session state.

    Provides thread-safe access to the current AirPlay state
    and allows the viewer to check if it should pause.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._state = STATE_IDLE
        self._client_name = None
        self._callbacks = []
        self._initialized = True

    @property
    def state(self):
        return self._state

    @property
    def client_name(self):
        return self._client_name

    @property
    def is_active(self):
        """Returns True if AirPlay is currently streaming."""
        return self._state in (STATE_CONNECTED, STATE_STREAMING)

    @property
    def is_streaming(self):
        """Returns True if AirPlay is actively streaming video."""
        return self._state == STATE_STREAMING

    def update_state(self, state, client_name=None):
        """Update the AirPlay state and notify callbacks."""
        old_state = self._state
        self._state = state
        self._client_name = client_name

        if old_state != state:
            logger.info(
                f'AirPlay state changed: {old_state} -> {state}, '
                f'client: {client_name}'
            )
            for callback in self._callbacks:
                try:
                    callback(state, client_name)
                except Exception as e:
                    logger.error(f'Error in AirPlay callback: {e}')

    def register_callback(self, callback):
        """Register a callback for state changes."""
        self._callbacks.append(callback)

    def unregister_callback(self, callback):
        """Unregister a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)


class AirPlaySubscriber(Thread):
    """
    Thread that subscribes to AirPlay events from the airplay container.
    """

    def __init__(self, redis_connection):
        Thread.__init__(self)
        self.daemon = True
        self.context = zmq.Context()
        self.redis_connection = redis_connection
        self.state_manager = AirPlayStateManager()
        self.running = True

    def run(self):
        """Main subscriber loop."""
        # Subscribe to AirPlay events from the server
        socket = self.context.socket(zmq.PULL)
        socket.bind('tcp://0.0.0.0:5560')

        logger.info('AirPlay subscriber started')
        self.redis_connection.set('airplay-subscriber-ready', int(True))

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        while self.running:
            try:
                socks = dict(poller.poll(1000))  # 1 second timeout
                if socket in socks:
                    message = socket.recv_json()
                    self._handle_message(message)
            except zmq.ZMQError as e:
                logger.error(f'ZMQ error in AirPlay subscriber: {e}')
            except Exception as e:
                logger.error(f'Error in AirPlay subscriber: {e}')

        socket.close()
        self.context.term()

    def _handle_message(self, message):
        """Handle incoming AirPlay event message."""
        msg_type = message.get('type')

        if msg_type == 'airplay_state':
            state = message.get('state', STATE_IDLE)
            client_name = message.get('client_name')
            self.state_manager.update_state(state, client_name)

            # Store state in Redis for API access
            self.redis_connection.set('airplay_state', state)
            if client_name:
                self.redis_connection.set('airplay_client', client_name)
            else:
                self.redis_connection.delete('airplay_client')

    def stop(self):
        """Stop the subscriber."""
        self.running = False


def get_airplay_state_manager():
    """Get the AirPlay state manager singleton."""
    return AirPlayStateManager()


def is_airplay_active():
    """Convenience function to check if AirPlay is active."""
    return AirPlayStateManager().is_active


def is_airplay_streaming():
    """Convenience function to check if AirPlay is streaming."""
    return AirPlayStateManager().is_streaming
