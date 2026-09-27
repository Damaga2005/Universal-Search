"""Provider and extension contracts (spec 019, formalized in phase 024).

A provider knows *where* files are; an extractor knows *how* to read one.
The search engine, the ranking and the GUI do not know which provider or
extractor produced a document, and adding a source does not modify any of
them.

Deliberate decision: **no runtime third-party plugin loading.** See
`docs/EXTENDING.md` for the reasoning and the full contract.
"""

from universal_search.providers.base import (
    AVAILABILITY,
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_CLOUD_ONLY,
    AVAILABILITY_UNAVAILABLE,
    CAPABILITIES,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    ERRORS,
    IDENTITY,
    INTERFACE_VERSION,
    METADATA,
    MAX_PROVIDER_ERRORS,
    STREAMING,
    WATCH,
    CancelToken,
    DocumentProvider,
    FileEntry,
    IgnoredPath,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderFile,
    ProviderInfo,
    ProviderRegistry,
    ProviderResult,
    ScanError,
    availability_from_attributes,
    collect_provider_files,
)
from universal_search.providers.local import LocalProvider
from universal_search.providers.network import NetworkProvider
from universal_search.providers.onedrive import OneDriveProvider
from universal_search.providers.registry import (
    REGISTRY,
    infos,
    provider_keys,
    register_builtins,
)
from universal_search.providers.removable import RemovableProvider

__all__ = [
    "AVAILABILITY",
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_CLOUD_ONLY",
    "AVAILABILITY_UNAVAILABLE",
    "CAPABILITIES",
    "CHANGE_DETECTION",
    "CONTENT",
    "ENUMERATE",
    "ERRORS",
    "IDENTITY",
    "INTERFACE_VERSION",
    "MAX_PROVIDER_ERRORS",
    "METADATA",
    "REGISTRY",
    "STREAMING",
    "WATCH",
    "CancelToken",
    "DocumentProvider",
    "FileEntry",
    "IgnoredPath",
    "LocalProvider",
    "NetworkProvider",
    "OneDriveProvider",
    "Provider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderFile",
    "ProviderInfo",
    "ProviderRegistry",
    "ProviderResult",
    "RemovableProvider",
    "ScanError",
    "availability_from_attributes",
    "collect_provider_files",
    "infos",
    "provider_keys",
    "register_builtins",
]
