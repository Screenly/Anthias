"""Controller mode: drive other Anthias screens over their v2 API.

A device with controller mode on keeps a list of paired screens
(:class:`~anthias_server.controller.models.Node`), talks to each over
HTTPS pinned to the screen's own certificate fingerprint, and can make
every screen's playlist match its own ("mirror publish"). All network
work runs in the Celery worker, so a slow or offline screen never
blocks the web UI.
"""
