"""OneDrive support: source classification, availability and cloud discovery.

Phase 007 in two stages:

* **Synced files** — files physically present under a detected OneDrive root
  run through the normal local pipeline but are classified with
  ``SourceKind.ONEDRIVE`` so every result carries a clear source label.
* **Cloud-only files** — Windows exposes non-downloaded OneDrive files as
  placeholder entries whose file attributes (``OFFLINE`` /
  ``RECALL_ON_DATA_ACCESS`` / ``RECALL_ON_OPEN``) mark them as not being
  physically present. Their metadata is indexable; their content is never
  read implicitly, because opening such a file would silently download it.
  Content access requires an explicit size limit set by the user.

Privacy: no network call, SDK or proprietary backend exists in this module —
discovery and availability come purely from the filesystem the OneDrive sync
client already maintains, and nothing is ever uploaded.

Detection (deterministic across processes, so worker/CLI/GUI agree):

1. environment variables ``OneDrive``, ``OneDriveConsumer``,
   ``OneDriveCommercial`` (set by the OneDrive client);
2. ``%USERPROFILE%\\OneDrive*`` directories;
3. fallback: any path component starting with ``OneDrive`` (case-insensitive),
   which keeps classification stable when the client env is absent.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from universal_search.domain.document import SourceKind
from universal_search.domain.extraction import ExtractionResult
from universal_search.providers.base import (
    AVAILABILITY,
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_CLOUD_ONLY,
    AVAILABILITY_UNAVAILABLE,
    CHANGE_DETECTION,
    CLOUD_MASK,
    ENUMERATE,
    ERRORS,
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    FILE_ATTRIBUTE_RECALL_ON_OPEN,
    IDENTITY,
    INTERFACE_VERSION,
    MAX_PROVIDER_ERRORS,
    METADATA,
    STREAMING,
    CancelToken,
    FileEntry,
    IgnoredPath,
    ProviderError,
    ProviderFile,
    ScanError,
    availability_from_attributes,
)
from universal_search.providers.ignore import IgnoreRules
from universal_search.providers.local import read_local_content, scan_local

# The availability constants and the Windows attribute bits are imported
# from providers/base.py above, which keeps this module's public names
# (onedrive.AVAILABILITY_CLOUD_ONLY, od.FILE_ATTRIBUTE_OFFLINE, …)
# working for existing callers. __all__ marks them as intentional
# re-exports for pyflakes.
__all__ = [
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_CLOUD_ONLY",
    "AVAILABILITY_UNAVAILABLE",
    "CLOUD_MASK",
    "FILE_ATTRIBUTE_OFFLINE",
    "FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS",
    "FILE_ATTRIBUTE_RECALL_ON_OPEN",
    "OneDriveFile",
    "OneDriveProvider",
    "allow_content_read",
    "availability_from_attributes",
    "availability_of",
    "is_onedrive_path",
    "onedrive_roots",
    "source_for_path",
]

ENV_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")

MB = 1_000_000

_roots_cache_key: tuple[str | None, ...] | None = None
_roots_cache: tuple[Path, ...] = ()


def onedrive_roots() -> tuple[Path, ...]:
    """Detected OneDrive sync roots on this machine."""
    global _roots_cache, _roots_cache_key
    key = tuple(os.environ.get(var) for var in ENV_VARS) + (
        os.environ.get("USERPROFILE"),
    )
    if key == _roots_cache_key:
        return _roots_cache
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        normalized = str(path).lower().rstrip("\\/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            roots.append(Path(path))

    for var in ENV_VARS:
        value = os.environ.get(var)
        if value:
            add(value)
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    try:
        for candidate in Path(profile).glob("OneDrive*"):
            if candidate.is_dir():
                add(str(candidate))
    except OSError:
        pass
    _roots_cache_key = key
    _roots_cache = tuple(roots)
    return _roots_cache


def is_onedrive_path(path: Path | str) -> bool:
    """Whether ``path`` lives inside a OneDrive sync root.

    Textual comparison only — no ``resolve()`` per file, so classifying a
    large tree costs nothing measurable.
    """
    text = str(path).lower().rstrip("\\/")
    for root in onedrive_roots():
        root_text = str(root).lower().rstrip("\\/")
        if text == root_text or text.startswith(root_text + "\\"):
            return True
    return any(part.lower().startswith("onedrive") for part in Path(path).parts)


def source_for_path(path: Path | str) -> SourceKind:
    """Provider classification for one path (the only place it is decided)."""
    return (
        SourceKind.ONEDRIVE
        if is_onedrive_path(path)
        else SourceKind.LOCAL
    )


def availability_of(path: Path | str) -> str:
    """Current availability of one path (``unavailable`` when it cannot be stat'ed)."""
    try:
        attributes = getattr(os.stat(path), "st_file_attributes", None)
    except OSError:
        return AVAILABILITY_UNAVAILABLE
    return availability_from_attributes(attributes)


def allow_content_read(
    availability: str, size: int, download_max_mb: float
) -> bool:
    """Single decision point for reading content of a (possibly) cloud file.

    * locally available files are always readable;
    * cloud-only files are readable only when the user configured an
      explicit download limit (``download_max_mb > 0``) **and** the file
      fits inside it — huge files are never silently downloaded.
    """
    if availability == AVAILABILITY_AVAILABLE:
        return True
    if availability != AVAILABILITY_CLOUD_ONLY:
        return False  # unavailable/offline: never read
    limit = download_max_mb * MB
    return limit > 0 and size <= limit


@dataclass(frozen=True, slots=True)
class OneDriveFile:
    """Metadata + availability state for one discovered OneDrive file."""

    path: Path
    size: int
    availability: str
    modified_at: datetime | None
    mtime_ns: int = 0


class OneDriveProvider:
    """Phase B provider: discover OneDrive files including cloud-only ones.

    It satisfies :class:`universal_search.providers.base.Provider` and
    exposes metadata plus availability without touching content.

    Declared capabilities (spec 019, formalized in phase 024): enumeration,
    metadata, availability and identity come from the filesystem
    attributes; **content is optional** and only read when the caller
    explicitly allows a download, so a cloud-only placeholder is never
    fetched by surprise. Phase 024 adds bounded error reporting and
    streaming enumeration.
    """

    key = "onedrive"
    version = "1.1"
    interface_version = INTERFACE_VERSION
    capabilities = frozenset(
        {
            ENUMERATE,
            METADATA,
            AVAILABILITY,
            IDENTITY,
            CHANGE_DETECTION,
            ERRORS,
            STREAMING,
        }
    )

    def __init__(
        self,
        rules: IgnoreRules | None = None,
        download_max_mb: float = 0.0,
    ) -> None:
        self.rules = rules
        self.download_max_mb = download_max_mb
        # Total enumeration errors seen in the last pass (including the ones
        # bounded away); read by the indexer to surface dropped errors.
        self._enumeration_errors = 0

    def available(self) -> bool:
        """True when at least one OneDrive root is configured on this PC."""
        return bool(onedrive_roots())

    def discover(
        self, root: Path
    ) -> Iterator[OneDriveFile | ScanError | IgnoredPath]:
        for item in scan_local(root, self.rules):
            if isinstance(item, FileEntry):
                yield OneDriveFile(
                    path=item.path,
                    size=item.size,
                    availability=availability_from_attributes(item.attributes),
                    modified_at=item.modified_at,
                    mtime_ns=item.mtime_ns,
                )
            else:
                yield item

    def iter_files(
        self, root: Path, cancel: CancelToken | None = None
    ) -> Iterator[ProviderFile | ProviderError | IgnoredPath]:
        """Streaming enumeration of a OneDrive root.

        Availability comes from the Windows attributes (cloud-only
        placeholders are reported as ``cloud_only``); errors are reported
        as :class:`ProviderError`, bounded by the shared cap.
        """
        errors = 0
        for item in self.discover(root):
            if cancel is not None and cancel.cancelled:
                return
            if isinstance(item, IgnoredPath):
                yield item
                continue
            if isinstance(item, ScanError):
                errors += 1
                self._enumeration_errors = errors
                if errors <= MAX_PROVIDER_ERRORS:
                    yield ProviderError(self.key, item.path, item.message)
                continue
            yield ProviderFile(
                provider=self.key,
                path=item.path,
                size=item.size,
                mtime_ns=item.mtime_ns,
                created_at=None,
                modified_at=item.modified_at,
                availability=item.availability,
                attributes=None,
            )

    def read_content(self, entry: OneDriveFile) -> ExtractionResult:
        """Explicit, controlled content access for one file.

        Cloud-only files are only downloaded when the user configured a
        limit that covers the file; otherwise this returns an explicit
        error and reads nothing.
        """
        if allow_content_read(entry.availability, entry.size, self.download_max_mb):
            return read_local_content(entry.path)
        if entry.availability == AVAILABILITY_CLOUD_ONLY:
            if self.download_max_mb <= 0:
                return ExtractionResult(
                    error="onedrive: cloud-only file, content not downloaded "
                    "(set an explicit download limit to index it)"
                )
            return ExtractionResult(
                error=f"onedrive: file of {entry.size} B exceeds the "
                f"download limit of {int(self.download_max_mb * MB)} B"
            )
        return ExtractionResult(
            error=f"onedrive: file is {entry.availability}; content unreadable"
        )
