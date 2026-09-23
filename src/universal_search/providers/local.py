import hashlib
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.domain.extraction import ExtractionResult
from universal_search.extractors import extract
from universal_search.providers.base import (
    AVAILABILITY,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    IDENTITY,
    METADATA,
    FileEntry,
    IgnoredPath,
    ScanError,
)
from universal_search.providers.ignore import IgnoreRules


class LocalProvider:
    """The filesystem provider, declared so it can be registered (019).

    The scanner itself is the module-level :func:`scan_local` (the indexer
    uses it directly and has for every phase); this class is the formal
    face of the same implementation, so the registry can describe it
    without the rest of the application having to know which is which.
    """

    key = "local"
    version = "1.0"
    capabilities = frozenset(
        {ENUMERATE, METADATA, CONTENT, CHANGE_DETECTION, AVAILABILITY, IDENTITY}
    )

    def __init__(self, rules: IgnoreRules | None = None) -> None:
        self.rules = rules

    def available(self) -> bool:
        return True

    def discover(self, root: Path) -> Iterable[Document]:
        return discover_local(root, self.rules)


def read_local_content(path: Path) -> ExtractionResult:
    """Extract a file's content through the extractor registry."""
    return extract(path)


def scan_local(
    root: Path, rules: IgnoreRules | None = None
) -> Iterable[FileEntry | ScanError]:
    """Walk ``root`` yielding filesystem metadata only — content is never read.

    Symlinked directories are never descended into, which prevents cycles.
    Inaccessible files and directories are reported as :class:`ScanError`
    instead of aborting the scan.
    """
    rules = rules or IgnoreRules.defaults()
    root_path = Path(root)
    try:
        stack = [root_path.resolve()]
    except OSError as exc:
        yield ScanError(root_path, f"{type(exc).__name__}: {exc}")
        return
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda entry: entry.name.lower())
        except OSError as exc:
            yield ScanError(Path(directory), f"{type(exc).__name__}: {exc}")
            continue
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    if rules.ignores_directory(entry.name):
                        yield IgnoredPath(entry_path, True)
                    else:
                        stack.append(entry_path)
                    continue
                if entry.is_symlink() or not entry.is_file():
                    continue
                if rules.ignores_file(entry.name):
                    yield IgnoredPath(entry_path, False)
                    continue
                stat = entry.stat()
            except OSError as exc:
                yield ScanError(entry_path, f"{type(exc).__name__}: {exc}")
                continue
            yield FileEntry(
                path=entry_path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                attributes=getattr(stat, "st_file_attributes", None),
            )


def discover_local(
    root: Path, rules: IgnoreRules | None = None
) -> Iterable[Document]:
    """Yield fully populated documents for a tree.

    This reads every supported file eagerly. Callers that need incremental
    behaviour should use ``Indexer.index_root``, which only extracts content
    for files that actually changed.
    """
    for item in scan_local(root, rules):
        if not isinstance(item, FileEntry):
            continue
        outcome = read_local_content(item.path)
        content = outcome.text
        yield Document(
            id=document_id_for(SourceKind.LOCAL, item.path),
            source=SourceKind.LOCAL,
            path=item.path,
            name=item.path.name,
            extension=item.path.suffix.lower(),
            size=item.size,
            created_at=item.created_at,
            modified_at=item.modified_at,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None,
        )
