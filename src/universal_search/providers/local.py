import hashlib
import os
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.domain.extraction import ExtractionLimits, ExtractionResult
from universal_search.extractors import extract
from universal_search.extractors.base import CancelCheck
from universal_search.providers.base import (
    AVAILABILITY,
    CHANGE_DETECTION,
    CONTENT,
    ENUMERATE,
    ERRORS,
    IDENTITY,
    INTERFACE_VERSION,
    METADATA,
    MAX_PROVIDER_ERRORS,
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


class LocalProvider:
    """The filesystem provider, declared so it can be registered (019).

    The scanner itself is the module-level :func:`scan_local` (the indexer
    uses it directly and has for every phase); this class is the formal
    face of the same implementation, so the registry can describe it
    without the rest of the application having to know which is which.

    Phase 024 adds the streaming :meth:`iter_files` contract: metadata
    namespaced by ``key = "local"``, bounded error reporting and a
    cooperative cancel token, on top of the same scanner that has always
    been used (symlinks are never followed, the root is resolved, and
    unreadable entries are reported instead of aborting the scan).
    """

    key = "local"
    version = "1.1"
    interface_version = INTERFACE_VERSION
    capabilities = frozenset(
        {
            ENUMERATE, METADATA, CONTENT, CHANGE_DETECTION, AVAILABILITY,
            IDENTITY, ERRORS, STREAMING,
        }
    )

    def __init__(self, rules: IgnoreRules | None = None) -> None:
        self.rules = rules
        # Total enumeration errors seen in the last pass (including the ones
        # bounded away); read by the indexer to surface dropped errors.
        self._enumeration_errors = 0

    def available(self) -> bool:
        return True

    def discover(self, root: Path) -> Iterable[Document]:
        return discover_local(root, self.rules)

    def iter_files(
        self, root: Path, cancel: CancelToken | None = None
    ) -> Iterator[ProviderFile | ProviderError | IgnoredPath]:
        """Streaming enumeration of ``root`` with the same policy as the indexer.

        Yields :class:`ProviderFile` metadata for every discovered file,
        :class:`IgnoredPath` for rule-skipped entries (so callers can count
        them) and at most ``MAX_PROVIDER_ERRORS`` :class:`ProviderError`
        entries; the scan never aborts on an unreadable file or directory.
        """
        errors = 0
        for item in scan_local(root, self.rules):
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
                created_at=item.created_at,
                modified_at=item.modified_at,
                availability=availability_from_attributes(item.attributes),
                attributes=item.attributes,
            )


def read_local_content(
    path: Path,
    *,
    limits: ExtractionLimits | None = None,
    cancel: CancelCheck | None = None,
) -> ExtractionResult:
    """Extract a file's content through the extractor registry.

    Phase 025: the indexer's resource ``limits`` and cooperative
    ``cancel`` are forwarded, so one pass can bound and stop every
    extraction it runs.
    """
    return extract(path, limits=limits, cancel=cancel)


#: Phase 049. No real directory tree is 500 deep. Windows' own path limit is
#: far higher, so this is about cycles that the reparse-point checks cannot see,
#: not about legitimate depth -- and exceeding it is reported, not swallowed.
MAX_SCAN_DEPTH = 500


def scan_local(
    root: Path, rules: IgnoreRules | None = None
) -> Iterable[FileEntry | ScanError]:
    """Walk ``root`` yielding filesystem metadata only — content is never read.

    Symlinked directories are never descended into. Inaccessible files and
    directories are reported as :class:`ScanError` instead of aborting the scan.

    **Cycle defence, added in phase 049.** Neither of the checks above stops a
    **Windows junction**: a junction is not a symlink, so ``is_symlink()`` is
    False, and ``follow_symlinks=False`` compares the reparse point rather than
    the target. A junction pointing at an ancestor is walked again, and again.
    The walk is a generator, so it does not exhaust memory -- it just yields
    more documents per pass, and the indexer writes a row for each.

    So two bounds are declared here, and both are reported rather than applied
    silently:

    * :data:`MAX_SCAN_DEPTH` -- counted from the scanned root, not from the
      volume root, so it means the same thing wherever the user keeps their
      files. No real directory tree is 500 deep, and one that
      is gets a :class:`ScanError` naming the path instead of a partial index
      with no explanation;
    * a ``visited`` set of ``(st_dev, st_ino)`` identities, so a second route to
      the same directory is walked once. On Windows that is the volume serial
      plus the file index, which is what the platform offers in place of a
      device/inode pair.
    """
    rules = rules or IgnoreRules.defaults()
    root_path = Path(root)
    try:
        stack: list[tuple[Path, int]] = [(root_path.resolve(), 0)]
    except OSError as exc:
        yield ScanError(root_path, f"{type(exc).__name__}: {exc}")
        return
    visited: set[tuple[int, int]] = set()
    while stack:
        directory, depth = stack.pop()
        if depth > MAX_SCAN_DEPTH:
            yield ScanError(
                directory,
                f"depth {depth} below the scanned root exceeds the "
                f"{MAX_SCAN_DEPTH}-level bound (MAX_SCAN_DEPTH); raise it if "
                f"this tree is legitimate",
            )
            continue
        try:
            directory_stat = directory.stat()
        except OSError as exc:
            yield ScanError(Path(directory), f"{type(exc).__name__}: {exc}")
            continue
        identity = (directory_stat.st_dev, directory_stat.st_ino)
        if identity in visited:
            # Reached by a second route -- a junction, or a mount point loop.
            # Skipping it is correct and is what makes the bound a bound.
            continue
        visited.add(identity)
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
                        stack.append((entry_path, depth + 1))
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
