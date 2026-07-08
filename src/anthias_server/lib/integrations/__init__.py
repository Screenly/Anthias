"""Inbound content-import providers.

The mirror image of ``lib.screenly_migration`` (which pushes Anthias
assets *out* to Screenly): these providers pull media *in* from a
third-party signage platform and recreate each item as an Anthias
``Asset``.

Every provider implements the transport-agnostic ``ImportProvider``
interface (``base.py``) and is registered in ``registry.py``. The API
views, the settings wizard and the ``import_content`` management command
all speak only that interface + the neutral ``RemoteMediaItem`` /
``ImportOutcome`` dataclasses, so adding a provider is a new module plus
one registry entry, with no change to the endpoints, UI, or CLI. Providers
may be REST *or* GraphQL backed; nothing outside a provider module assumes
a transport.
"""
