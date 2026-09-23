"""Provider and extension contracts (spec 019).

Two extension points, both **internal registries**: providers enumerate
storage, extractors turn files into text. They are separate concerns — a
provider knows where a file is, an extractor knows how to read it — and
neither the ranking, the search engine nor the GUI knows which one is in
play.

Deliberate decision (documented in ``docs/EXTENDING.md``): **no runtime
third-party plugin loading**. Loading arbitrary code at runtime would mean
executing untrusted code with access to the user's private index, which is
exactly the threat model phase 018 refuses. Adding a provider or extractor
here is a code change, reviewed like any other; adding a *source* does not
touch the search engine.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from universal_search.domain.document import Document

# -- capabilities ---------------------------------------------------------------

# What a provider can do. Providers advertise these instead of the rest of
# the application guessing from the source name: a NAS provider may
# enumerate and give metadata but not read content, a removable one may be
# present but unavailable, and the indexer can skip it accordingly.
ENUMERATE = "enumerate"
METADATA = "metadata"
CONTENT = "content"
CHANGE_DETECTION = "change_detection"
AVAILABILITY = "availability"
IDENTITY = "identity"

CAPABILITIES: tuple[str, ...] = (
    ENUMERATE,
    METADATA,
    CONTENT,
    CHANGE_DETECTION,
    AVAILABILITY,
    IDENTITY,
)

# Bumped when a provider or extractor interface changes shape. A provider
# registered against an older interface is reported, not silently used.
INTERFACE_VERSION = 1


@runtime_checkable
class DocumentProvider(Protocol):
    """A source of documents under a root."""

    key: str
    version: str
    capabilities: frozenset[str]

    def discover(self, root: Path) -> Iterable[Document]:
        ...

    def available(self) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """Inspectable description of one registered provider."""

    key: str
    kind: str
    version: str
    interface_version: int
    capabilities: tuple[str, ...]
    available: bool
    detail: str

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "kind": self.kind,
            "version": self.version,
            "interface_version": self.interface_version,
            "capabilities": list(self.capabilities),
            "available": self.available,
            "detail": self.detail,
        }


@dataclass
class ProviderRegistry:
    """Registration, lookup and inspection of providers.

    Failure isolation is a property of this type: a provider that raises
    while being queried is reported as unavailable instead of taking the
    application down, and a duplicate key is refused rather than silently
    replacing a working provider.
    """

    _providers: dict[str, object] = field(default_factory=dict)

    def register(self, provider, *, kind: str = "filesystem", detail: str = "") -> None:
        key = str(getattr(provider, "key", "")).strip()
        if not key:
            raise ValueError("a provider must expose a non-empty 'key'")
        if key in self._providers:
            raise ValueError(f"provider already registered: {key}")
        if not isinstance(provider, DocumentProvider):
            raise TypeError(
                f"{key} does not satisfy DocumentProvider "
                f"(discover/available/key/version/capabilities)"
            )
        unknown = set(getattr(provider, "capabilities", ())) - set(CAPABILITIES)
        if unknown:
            raise ValueError(f"{key} declares unknown capabilities: {sorted(unknown)}")
        self._providers[key] = (provider, kind, detail)

    def get(self, key: str):
        entry = self._providers.get(key)
        return entry[0] if entry else None

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def infos(self) -> tuple[ProviderInfo, ...]:
        """Every provider, with availability resolved defensively."""
        described: list[ProviderInfo] = []
        for key in self.keys():
            provider, kind, detail = self._providers[key]
            try:
                available = bool(provider.available())
                note = detail
            except Exception as exc:
                # A provider that cannot answer is reported, not raised.
                available = False
                note = f"{detail} (availability check failed: {type(exc).__name__})"
            described.append(
                ProviderInfo(
                    key=key,
                    kind=kind,
                    version=str(getattr(provider, "version", "0")),
                    interface_version=INTERFACE_VERSION,
                    capabilities=tuple(sorted(getattr(provider, "capabilities", ()))),
                    available=available,
                    detail=note,
                )
            )
        return tuple(described)

    def for_capability(self, capability: str) -> tuple[str, ...]:
        return tuple(
            info.key
            for info in self.infos()
            if info.supports(capability) and info.available
        )


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Filesystem metadata for one file, discovered before any content is read."""

    path: Path
    size: int
    mtime_ns: int
    created_at: datetime | None
    modified_at: datetime | None
    # Raw Windows st_file_attributes (None on other platforms); used to
    # detect cloud-only placeholders without reading (and downloading) them.
    attributes: int | None = None


@dataclass(frozen=True, slots=True)
class ScanError:
    """A file or directory that could not be examined during discovery."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class IgnoredPath:
    """A path skipped because of ignore rules."""

    path: Path
    is_directory: bool
