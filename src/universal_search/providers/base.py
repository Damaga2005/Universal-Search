"""Provider and extension contracts (spec 019, formalized in phase 024).

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

Phase 024 formalized the contract:

* **Capabilities** are a closed vocabulary (``CAPABILITIES``) a provider
  declares from; the registry rejects unknown names and incompatible
  interface versions instead of guessing.
* **Identity** is provider-namespaced: ``document_id_for(provider_key, path)``
  and the database uniqueness contract is ``(source, path)``, so two
  providers may own the same path without changing search or ranking.
* **Enumeration** is streaming (:meth:`Provider.iter_files`) with a
  cooperative :class:`CancelToken`, bounded error reporting and
  availability states shared with the OneDrive layer.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from universal_search.domain.document import Document, document_id_for

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
# Phase 024: bounded per-entry error reporting, filesystem change
# notifications and constant-memory streaming enumeration.
ERRORS = "errors"
WATCH = "watch"
STREAMING = "streaming"

CAPABILITIES: tuple[str, ...] = (
    ENUMERATE,
    METADATA,
    CONTENT,
    CHANGE_DETECTION,
    AVAILABILITY,
    IDENTITY,
    ERRORS,
    WATCH,
    STREAMING,
)

# Bumped when a provider or extractor interface changes shape. A provider
# registered against an older interface is reported, not silently used.
INTERFACE_VERSION = 2

# How many enumeration errors one provider may surface per pass. A share
# with thousands of unreadable entries must not grow the report without
# bound; the indexer counts the rest.
MAX_PROVIDER_ERRORS = 100

# -- availability states --------------------------------------------------------
# Shared with the OneDrive layer: a file is locally available, a cloud-only
# placeholder (reading it would download it), or unavailable/offline.

AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_CLOUD_ONLY = "cloud_only"
AVAILABILITY_UNAVAILABLE = "unavailable"

# os.stat_result.st_file_attributes bits (Windows file attributes).
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
CLOUD_MASK = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


def availability_from_attributes(attributes: int | None) -> str:
    """Map Windows file attributes to an availability state.

    Placeholder attributes mean the file is a cloud-only entry: reading it
    would trigger a download, so callers must treat it as metadata-only
    unless the user explicitly opted in. ``None`` (non-Windows or a plain
    file) means locally available.
    """
    if attributes is None:
        return AVAILABILITY_AVAILABLE
    return (
        AVAILABILITY_CLOUD_ONLY
        if attributes & CLOUD_MASK
        else AVAILABILITY_AVAILABLE
    )


# -- cancellation ----------------------------------------------------------------


class CancelToken:
    """Cooperative cancellation handle for long enumerations.

    A provider checks ``cancelled`` between entries and stops cleanly when
    it is set; the indexer never kills a provider mid-iteration.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# -- structured capability declaration -------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Structured declaration of what a provider can do.

    The vocabulary is the module-level ``CAPABILITIES`` tuple; this type
    lets a provider declare its set explicitly and derive the frozenset the
    registry validates. ``watch`` is part of the vocabulary but no built-in
    provider claims it yet: change notifications are a worker concern.
    """

    enumerate: bool = False
    metadata: bool = False
    content: bool = False
    identity: bool = False
    change_detection: bool = False
    availability: bool = False
    errors: bool = False
    watch: bool = False
    streaming: bool = False

    def as_frozenset(self) -> frozenset[str]:
        return frozenset(name for name in CAPABILITIES if getattr(self, name))


# -- provider protocol --------------------------------------------------------------


@runtime_checkable
class Provider(Protocol):
    """Phase 024 provider contract: streaming enumeration plus availability."""

    key: str
    version: str
    capabilities: frozenset[str]
    interface_version: int

    def iter_files(
        self, root: Path, cancel: CancelToken | None = None
    ) -> Iterator["ProviderFile | ProviderError | IgnoredPath"]:
        ...

    def available(self) -> bool:
        ...


@runtime_checkable
class DocumentProvider(Protocol):
    """A source of documents under a root (legacy ``discover`` contract).

    Phase 024 providers implement :class:`Provider` instead; this protocol
    stays so existing discover-only providers keep registering.
    """

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
        # Method-based check (not isinstance): a runtime_checkable protocol
        # would also demand the data members, which legacy providers that
        # predate 'interface_version' do not have.
        enumerates = hasattr(provider, "iter_files") or hasattr(provider, "discover")
        if not enumerates or not hasattr(provider, "available"):
            raise TypeError(
                f"{key} does not satisfy the provider contract "
                "(iter_files or discover, plus available/key/version/capabilities)"
            )
        declared = getattr(provider, "interface_version", None)
        if declared is not None and declared != INTERFACE_VERSION:
            raise ValueError(
                f"{key} declares interface version {declared}; "
                f"this build supports {INTERFACE_VERSION}"
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
                    # Report the provider's declared version; a legacy
                    # provider without the attribute falls back to the build.
                    interface_version=getattr(
                        provider, "interface_version", INTERFACE_VERSION
                    ),
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


# -- enumeration items --------------------------------------------------------------


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
class ProviderFile:
    """One enumerated file, namespaced by the provider that owns it.

    ``provider`` is the canonical source discriminator: it is what the
    indexer stores in ``documents.source`` and what namespaces the stable
    identity, so two providers can own the same path.
    """

    provider: str
    path: Path
    size: int
    mtime_ns: int
    created_at: datetime | None
    modified_at: datetime | None
    availability: str = AVAILABILITY_AVAILABLE
    attributes: int | None = None

    @property
    def source(self) -> str:
        return self.provider

    @property
    def document_id(self) -> str:
        return document_id_for(self.provider, self.path)


@dataclass(frozen=True, slots=True)
class ProviderError:
    """One file or directory a provider could not examine during enumeration."""

    provider: str
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Materialized enumeration: files plus bounded errors, one provider."""

    provider: str
    files: tuple[ProviderFile, ...]
    errors: tuple[ProviderError, ...]


def collect_provider_files(
    provider,
    root: Path,
    cancel: CancelToken | None = None,
    *,
    max_errors: int = MAX_PROVIDER_ERRORS,
) -> ProviderResult:
    """Drain ``provider.iter_files`` into a bounded, inspectable result.

    Errors are capped at ``max_errors`` so a share full of unreadable
    entries cannot grow the report without bound; ignored paths are not
    part of the contract and are dropped here.
    """
    files: list[ProviderFile] = []
    errors: list[ProviderError] = []
    for item in provider.iter_files(root, cancel):
        if isinstance(item, ProviderError):
            if len(errors) < max_errors:
                errors.append(item)
        elif isinstance(item, ProviderFile):
            files.append(item)
    return ProviderResult(str(getattr(provider, "key", "")), tuple(files), tuple(errors))


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
