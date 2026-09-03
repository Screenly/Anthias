"""Warn once per fault, then stay quiet until it clears.

A device-level fault — no kernel boot id, a Redis that will not answer
— is a property of the device, not of the reading that noticed it. The
under-voltage watcher, the storage-health watcher, the now-playing
reporter and every page render funnel through these paths, so an
unthrottled warning buries the rest of the journal: GH #3268 measured
that class of repetition evicting crash diagnostics inside a day.

An instance per module rather than one shared set, for two reasons
that both bite. The line keeps its own module's logger name, so the
journal still says which subsystem noticed; and the keys stay
namespaced, which matters because ``undervoltage`` and
``storage_health`` both use ``no_boot_id`` and neither may silence the
other.
"""

import logging


class WarnOnce:
    """WARNING the first time a key fails, DEBUG until it succeeds."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._seen: set[str] = set()

    def warn(
        self, key: str, message: str, exc: Exception | None = None
    ) -> None:
        log = self._logger.debug if key in self._seen else self._logger.warning
        self._seen.add(key)
        if exc is None:
            log(message)
        else:
            log('%s: %s', message, exc)

    def worked(self, key: str) -> None:
        """Re-arm ``key`` after a call succeeds.

        So a two-second blip at container start doesn't silence a
        genuinely different fault — a WRONGTYPE, a decode failure —
        for the life of the process.
        """
        self._seen.discard(key)

    def reset(self) -> None:
        """Forget every latched key. For tests, which would otherwise
        let the first failure in a run behave differently from the
        rest."""
        self._seen.clear()
