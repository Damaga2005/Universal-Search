"""Provider and extension contracts (spec 019).

A provider knows *where* files are; an extractor knows *how* to read one.
The search engine, the ranking and the GUI do not know which provider or
extractor produced a document, and adding a source does not modify any of
them.

Deliberate decision: **no runtime third-party plugin loading.** See
`docs/EXTENDING.md` for the reasoning and the full contract.
"""

from universal_search.providers.base import (
    AVAILABILITY,
    CAPABILITIES,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    IDENTITY,
    INTERFACE_VERSION,
    METADATA,
    DocumentProvider,
    FileEntry,
    IgnoredPath,
    ProviderInfo,
    ProviderRegistry,
    ScanError,
)
from universal_search.providers.registry import (
    REGISTRY,
    infos,
    provider_keys,
    register_builtins,
)

__all__ = [
    "AVAILABILITY",
    "CAPABILITIES",
    "CHANGE_DETECTION",
    "CONTENT",
    "ENUMERATE",
    "IDENTITY",
    "INTERFACE_VERSION",
    "METADATA",
    "REGISTRY",
    "DocumentProvider",
    "FileEntry",
    "IgnoredPath",
    "ProviderInfo",
    "ProviderRegistry",
    "ScanError",
    "infos",
    "provider_keys",
    "register_builtins",
]
