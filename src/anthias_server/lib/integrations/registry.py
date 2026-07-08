"""Registry of available import providers.

Adding a provider is a two-line change here (import it, add an instance
to ``_PROVIDERS``); the API views, settings page and CLI discover it
through :func:`get_provider` / :func:`list_provider_meta` without any
further edits.
"""

from __future__ import annotations

from typing import Any

from .base import ImportProvider
from .optisigns import OptiSignsProvider
from .screencloud import ScreenCloudProvider
from .yodeck import YodeckProvider

# Providers are stateless (their HTTP session is module-level), so a
# single shared instance each is fine.
_PROVIDERS: dict[str, ImportProvider] = {
    provider.key: provider
    for provider in (
        YodeckProvider(),
        ScreenCloudProvider(),
        OptiSignsProvider(),
    )
}


def get_provider(key: str) -> ImportProvider | None:
    """Return the provider registered under ``key``, or None."""
    return _PROVIDERS.get(key)


def _meta(provider: ImportProvider) -> dict[str, Any]:
    return {
        'key': provider.key,
        'label': provider.label,
        'description': provider.description,
        'token_help': provider.token_help,
    }


def get_provider_meta(key: str) -> dict[str, Any] | None:
    """Return display metadata for ``key`` (for the wizard), or None."""
    provider = _PROVIDERS.get(key)
    return _meta(provider) if provider is not None else None


def list_provider_meta() -> list[dict[str, Any]]:
    """Return display metadata for every provider (for the settings page)."""
    return [_meta(provider) for provider in _PROVIDERS.values()]
