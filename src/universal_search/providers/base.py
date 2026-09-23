from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from universal_search.domain.document import Document


@runtime_checkable
class DocumentProvider(Protocol):
    def discover(self, root: Path) -> Iterable[Document]:
        ...


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
