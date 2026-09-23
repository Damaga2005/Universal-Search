"""The registry of built-in providers (spec 019).

There is exactly one registry per process and it is filled with the
providers that ship with the application. Adding a source (NAS, removable
media, a second cloud drive) means implementing
:class:`~universal_search.providers.base.DocumentProvider` and registering
it here: the indexer, the ranking, the search engine and the GUI are not
modified, which is the compatibility promise of the phase.

See `docs/EXTENDING.md` for the full contract, the capabilities each
built-in provider declares, and why there is deliberately no dynamic
plugin loading.
"""

from universal_search.providers.base import (
    INTERFACE_VERSION,
    ProviderInfo,
    ProviderRegistry,
)
from universal_search.providers.local import LocalProvider
from universal_search.providers.onedrive import OneDriveProvider

REGISTRY = ProviderRegistry()


def register_builtins(registry: ProviderRegistry | None = None) -> ProviderRegistry:
    """Register the shipped providers (idempotent)."""
    target = registry if registry is not None else REGISTRY
    if not target.keys():
        target.register(
            LocalProvider(),
            kind="filesystem",
            detail="local filesystem, metadata before content",
        )
        target.register(
            OneDriveProvider(),
            kind="cloud",
            detail="OneDrive placeholders; content only when allowed",
        )
    return target


def infos() -> tuple[ProviderInfo, ...]:
    """Inspectable description of every registered provider."""
    return register_builtins().infos()


def provider_keys() -> tuple[str, ...]:
    return register_builtins().keys()


__all__ = [
    "INTERFACE_VERSION",
    "REGISTRY",
    "ProviderInfo",
    "ProviderRegistry",
    "infos",
    "provider_keys",
    "register_builtins",
]
