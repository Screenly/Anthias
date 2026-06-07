"""Fault-tolerant WhiteNoise middleware.

WhiteNoise scans STATIC_ROOT once at startup (``autorefresh=False``
in production). Stock behaviour lets any ``OSError`` from that scan
propagate, which takes uvicorn down during ASGI import — observed in
the field as ``OSError: [Errno 117] Structure needs cleaning`` when a
balena OTA rewrote the staticfiles image layer onto a device with
corrupted ext4 metadata (Sentry ANTHIAS-Y, 400+ events from one
crash-looping device).

A signage appliance must degrade, not brick: one unreadable admin
vendor file is not worth losing the API, the WebSocket endpoint, and
the asset server. Skip what the filesystem refuses to serve, keep
everything else, and emit a single ERROR log per startup so the
storage fault still lands in Sentry exactly once per boot — loud
enough to act on, quiet enough not to flood.
"""

import logging
import os
from collections.abc import Iterator

from whitenoise.middleware import WhiteNoiseMiddleware

logger = logging.getLogger(__name__)

_SKIP_LOG_EXAMPLES = 3


class ResilientWhiteNoiseMiddleware(WhiteNoiseMiddleware):
    """WhiteNoise whose startup scan survives unreadable entries.

    Mirrors ``whitenoise.base.WhiteNoise.update_files_dictionary`` /
    ``scantree`` (whitenoise 6.x — both are stable one-screen
    helpers) with per-entry ``OSError`` tolerance added. Everything
    else — response serving, headers, compression handling — is
    inherited untouched.
    """

    def update_files_dictionary(self, root: str, prefix: str) -> None:
        # Stock whitenoise computes the URL as ``prefix +
        # path[len(root):]`` and relies on its caller having appended
        # a trailing separator to ``root``. Don't depend on that:
        # normalise here so a ``root`` without the trailing sep can't
        # yield a ``/static//css/app.css`` double-slash URL that then
        # fails to match incoming requests.
        root_with_sep = os.path.join(root, '')
        skipped: list[tuple[str, OSError]] = []
        stat_cache = dict(self._scantree_tolerant(root_with_sep, skipped))
        for path in stat_cache:
            relative_path = path[len(root_with_sep) :]
            relative_url = relative_path.replace('\\', '/')
            url = prefix + relative_url
            self.add_file_to_dictionary(url, path, stat_cache=stat_cache)
        if skipped:
            examples = '; '.join(
                f'{path}: {exc}' for path, exc in skipped[:_SKIP_LOG_EXAMPLES]
            )
            logger.error(
                'Skipped %d unreadable static file(s)/dir(s) under %s — '
                'the filesystem reported errors (%s). The device storage '
                'likely needs attention (fsck/reflash); serving the '
                'remaining static files.',
                len(skipped),
                root,
                examples,
            )

    @classmethod
    def _scantree_tolerant(
        cls,
        root: str,
        skipped: list[tuple[str, OSError]],
    ) -> Iterator[tuple[str, os.stat_result]]:
        """``whitenoise.base.scantree``, but a directory that can't be
        listed or an entry that can't be stat'd is recorded in
        ``skipped`` instead of aborting the whole scan."""
        try:
            entries = list(os.scandir(root))
        except OSError as exc:
            skipped.append((root, exc))
            return
        for entry in entries:
            try:
                if entry.is_dir():
                    yield from cls._scantree_tolerant(entry.path, skipped)
                else:
                    yield entry.path, entry.stat()
            except OSError as exc:
                skipped.append((entry.path, exc))
