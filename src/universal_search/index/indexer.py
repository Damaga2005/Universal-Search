import hashlib
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from universal_search.domain.document import Document, document_id_for
from universal_search.domain.extraction import ExtractionResult
from universal_search.index.database import SearchDatabase
from universal_search.metrics import record_index
from universal_search.providers.base import IgnoredPath, ScanError
from universal_search.providers.ignore import IgnoreRules
from universal_search.providers.local import read_local_content, scan_local
from universal_search.providers.onedrive import (
    AVAILABILITY_AVAILABLE,
    allow_content_read,
    availability_from_attributes,
    source_for_path,
)


ContentReader = Callable[[Path], ExtractionResult]

# Reconciliation flushes a transaction every this many changed files: one
# amortized WAL write instead of a commit per row (profiled: a commit costs
# as much as reading and extracting the file itself). At most this much work
# stays uncommitted if the process dies mid-pass; graceful stops commit at
# the end of the pass because the final commit below always runs.
COMMIT_EVERY = 200


@dataclass
class IndexStats:
    """Outcome of one reconciliation pass over a tree."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: int = 0
    extraction_errors: int = 0
    cloud_only: int = 0

    @property
    def scanned(self) -> int:
        """Files that were seen and are represented in the index."""
        return self.created + self.updated + self.unchanged

    @property
    def total_seen(self) -> int:
        return self.scanned + self.errors

    def merge(self, other: "IndexStats") -> None:
        for name in (
            "created", "updated", "unchanged", "deleted",
            "ignored", "errors", "extraction_errors", "cloud_only",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
            "ignored": self.ignored,
            "errors": self.errors,
            "extraction_errors": self.extraction_errors,
            "cloud_only": self.cloud_only,
        }

    def summary(self) -> str:
        return (
            f"created={self.created} updated={self.updated} "
            f"unchanged={self.unchanged} deleted={self.deleted} "
            f"ignored={self.ignored} errors={self.errors} "
            f"extraction_errors={self.extraction_errors} "
            f"cloud_only={self.cloud_only}"
        )


class Indexer:
    def __init__(self, database: SearchDatabase) -> None:
        self.database = database
        self._connection: sqlite3.Connection | None = None

    def connection(self) -> sqlite3.Connection:
        """The one connection every write reuses.

        Profiled on Windows: opening a connection per document and then
        checkpointing it on last close cost ~12ms of pure overhead each
        (the WAL checkpoint alone was ~8ms), drowning the ~1ms of real
        SQL underneath. The connection opens lazily and lives until
        close(); readers elsewhere in the process are unaffected (WAL).
        """
        if self._connection is None:
            self._connection = self.database.connect()
        return self._connection

    def close(self) -> None:
        """Release the shared connection (idempotent)."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # -- single document ---------------------------------------------------

    def upsert(self, document: Document) -> None:
        connection = self.connection()
        try:
            self._upsert(connection, document)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _upsert(
        connection,
        document: Document,
        mtime_ns: int | None = None,
        run_id: int | None = None,
        availability: str = AVAILABILITY_AVAILABLE,
    ) -> None:
        previous = connection.execute(
            "SELECT id FROM documents WHERE path = ?", (str(document.path),)
        ).fetchone()
        stale_ids = {document.id}
        if previous is not None:
            stale_ids.add(previous["id"])
        for stale_id in stale_ids:
            connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (stale_id,))
        if mtime_ns is None:
            mtime_ns = (
                int(document.modified_at.timestamp() * 1_000_000_000)
                if document.modified_at
                else None
            )
        connection.execute("""
            INSERT INTO documents (id, source, path, name, extension, size,
                                   created_at, modified_at, content_hash, mtime_ns,
                                   last_seen_run, availability)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                id=excluded.id, source=excluded.source, name=excluded.name,
                extension=excluded.extension, size=excluded.size,
                created_at=excluded.created_at, modified_at=excluded.modified_at,
                content_hash=excluded.content_hash, mtime_ns=excluded.mtime_ns,
                last_seen_run=excluded.last_seen_run,
                availability=excluded.availability,
                indexed_at=CURRENT_TIMESTAMP
        """, (
            document.id, document.source.value, str(document.path), document.name,
            document.extension, document.size,
            document.created_at.isoformat() if document.created_at else None,
            document.modified_at.isoformat() if document.modified_at else None,
            document.content_hash,
            mtime_ns,
            run_id if run_id is not None else int(time.time_ns()),
            availability,
        ))
        connection.execute(
            "INSERT INTO documents_fts(document_id,name,path,content) VALUES (?,?,?,?)",
            (document.id, document.name, str(document.path), document.content or ""),
        )

    @staticmethod
    def _delete(connection, document_id: str) -> None:
        connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (document_id,))
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    # -- reconciliation ----------------------------------------------------

    def index_root(
        self,
        root: Path,
        *,
        rules: IgnoreRules | None = None,
        read_content: ContentReader | None = None,
        delay: float = 0.0,
        onedrive_download_mb: float = 0.0,
    ) -> IndexStats:
        """Reconcile everything indexed under ``root`` with the filesystem.

        Unchanged files (same size, mtime, source and availability) are
        skipped without reading their content. Changed files are
        re-extracted and replace their previous representation. Files that
        vanished from disk are removed from both the metadata table and the
        full-text index, whatever their source. The index database itself is
        never indexed.

        Cloud-only OneDrive placeholders are indexed as metadata without
        reading them (reading would trigger a download); content for them is
        only fetched when ``onedrive_download_mb > 0`` explicitly allows it
        and the file fits the limit.

        ``delay`` sleeps that many seconds after writing each changed file —
        a cooperative CPU/disk resource limit used by the background worker.
        """
        rules = rules or IgnoreRules.defaults()
        read_content = read_content or read_local_content
        stats = IndexStats()
        run_id = time.time_ns()
        root_path = Path(root).resolve()
        own_files = _own_database_files(self.database.path)
        started = time.perf_counter()
        with closing(self.database.connect()) as connection:
            writes_before = connection.total_changes
            pending = 0
            for item in scan_local(root_path, rules):
                if isinstance(item, IgnoredPath):
                    stats.ignored += 1
                    continue
                if isinstance(item, ScanError):
                    stats.errors += 1
                    continue
                if str(item.path) in own_files:
                    stats.ignored += 1
                    continue

                source = source_for_path(item.path)
                availability = availability_from_attributes(item.attributes)
                stored = connection.execute(
                    "SELECT id, size, mtime_ns, content_hash, source, availability "
                    "FROM documents WHERE path = ?",
                    (str(item.path),),
                ).fetchone()
                if (
                    stored is not None
                    and stored["size"] == item.size
                    and stored["mtime_ns"] == item.mtime_ns
                    and stored["source"] == source.value
                    and stored["availability"] == availability
                ):
                    stats.unchanged += 1
                    connection.execute(
                        "UPDATE documents SET last_seen_run = ? WHERE id = ?",
                        (run_id, stored["id"]),
                    )
                    pending += 1
                    if pending >= COMMIT_EVERY:
                        connection.commit()
                        pending = 0
                    continue

                content: str | None = None
                if allow_content_read(availability, item.size, onedrive_download_mb):
                    try:
                        outcome = read_content(item.path)
                    except Exception as exc:  # a broken reader must not stop the run
                        outcome = ExtractionResult(error=f"{type(exc).__name__}: {exc}")
                    if outcome.error is not None:
                        stats.extraction_errors += 1
                    content = outcome.text
                    if availability != AVAILABILITY_AVAILABLE and outcome.error is None:
                        availability = AVAILABILITY_AVAILABLE  # explicit download
                else:
                    # Cloud-only placeholder: metadata only. Reading it here
                    # would silently download the file (phase 007 rule).
                    stats.cloud_only += 1

                document = Document(
                    id=document_id_for(source, item.path),
                    source=source,
                    path=item.path,
                    name=item.path.name,
                    extension=item.path.suffix.lower(),
                    size=item.size,
                    created_at=item.created_at,
                    modified_at=item.modified_at,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if content is not None
                    else None,
                )
                if stored is None:
                    self._upsert(
                        connection, document,
                        mtime_ns=item.mtime_ns, run_id=run_id,
                        availability=availability,
                    )
                    stats.created += 1
                elif (
                    content is not None
                    and stored["content_hash"] is not None
                    and document.content_hash == stored["content_hash"]
                    and stored["source"] == source.value
                ):
                    # Content is identical: refresh metadata without touching FTS.
                    connection.execute(
                        """UPDATE documents SET size = ?, mtime_ns = ?, modified_at = ?,
                                  last_seen_run = ?, availability = ?,
                                  indexed_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (
                            item.size, item.mtime_ns,
                            item.modified_at.isoformat() if item.modified_at else None,
                            run_id, availability, stored["id"],
                        ),
                    )
                    stats.unchanged += 1
                else:
                    self._upsert(
                        connection, document,
                        mtime_ns=item.mtime_ns, run_id=run_id,
                        availability=availability,
                    )
                    stats.updated += 1
                pending += 1
                if pending >= COMMIT_EVERY:
                    connection.commit()
                    pending = 0
                if delay:
                    time.sleep(delay)

            self._delete_missing(connection, root_path, run_id, stats)
            connection.commit()
            db_writes = connection.total_changes - writes_before
        record_index(time.perf_counter() - started, stats.as_dict(), db_writes)
        return stats

    @staticmethod
    def _delete_missing(connection, root_path: Path, run_id: int, stats: IndexStats) -> None:
        root_text = str(root_path)
        # Range predicates can use the UNIQUE index on path. The path range
        # covers every source: a path belongs to exactly one source, so
        # classification changes can never leave orphan rows behind.
        stale = connection.execute(
            """SELECT id FROM documents
               WHERE path > ? AND path < ? AND last_seen_run < ?""",
            (root_text + os.sep, root_text + "\uffff", run_id),
        ).fetchall()
        for row in stale:
            Indexer._delete(connection, row["id"])
        stats.deleted += len(stale)


def _own_database_files(path: Path) -> set[str]:
    base = str(path)
    return {base, base + "-wal", base + "-shm"}
