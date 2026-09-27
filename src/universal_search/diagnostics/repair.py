"""Repair operations, and the line between safe and destructive (spec 015).

Two tiers, enforced in code and not only in the UI:

* **safe** — `reconcile`, `rebuild_intelligence`. They only add or refresh
  what the filesystem already says; running them twice changes nothing.
* **destructive** — `rebuild_fts`, `re_extract`, `rebuild_all`. They
  delete search rows or the whole database, so the API demands
  ``confirm=True`` and the CLI demands an explicit flag. There is no code
  path that drops data without that word being passed.

Every operation returns a :class:`RepairResult` describing what it did, in
counts a user can check afterwards.
"""

import hashlib
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from universal_search.domain.document import SourceKind, document_id_for
from universal_search.index.database import SearchDatabase, UnsupportedSchemaVersion
from universal_search.index.indexer import Indexer
from universal_search.providers.local import read_local_content

CONFIRMATION_REQUIRED = "this operation deletes data; pass confirm=True"
# Orphan/reference cleanup is deliberately cursor-paged.  A repair may see a
# large damaged FTS table, but its Python working set stays bounded.
ORPHAN_CLEANUP_BATCH_SIZE = 128


class ConfirmationRequired(RuntimeError):
    """Raised when a destructive repair is attempted without confirmation."""


class RepairBlocked(RuntimeError):
    """Raised when the index file is in use and cannot be replaced."""


@dataclass(frozen=True, slots=True)
class RepairResult:
    action: str
    changed: int
    detail: str
    skipped: int = 0
    application_files: int = 0
    source_files: int = 0

    @property
    def physical_files(self) -> int:
        return self.source_files

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "changed": self.changed,
            "skipped": self.skipped,
            "application_files": self.application_files,
            "source_files": self.source_files,
            "detail": self.detail,
        }


def reconcile(database: SearchDatabase, root: Path) -> RepairResult:
    """Run one indexing pass over ``root``. Safe and idempotent."""
    stats = Indexer(database).index_root(root)
    return RepairResult(
        action="reconcile",
        changed=stats.created + stats.updated + stats.deleted,
        skipped=stats.unchanged,
        detail=(
            f"{stats.created} created, {stats.updated} updated, "
            f"{stats.deleted} removed, {stats.unchanged} unchanged, "
            f"{stats.errors} errors"
        ),
    )


def rebuild_fts(
    database: SearchDatabase, *, confirm: bool = False
) -> RepairResult:
    """Remove orphaned search rows and re-read documents that lost theirs.

    Destructive: orphaned rows are deleted and files are re-extracted, so
    it requires ``confirm=True``.
    """
    if not confirm:
        raise ConfirmationRequired(CONFIRMATION_REQUIRED)
    removed = re_extracted = skipped = 0
    with closing(database.connect()) as connection:
        from universal_search.intelligence.graph import (
            MAX_TARGETED_RECORDS,
            mark_graph_dirty,
            scrub_reference_metadata,
        )

        # Cursor-page both sides of the repair.  No query below materializes
        # all damaged rows, and all batches share one transaction.
        orphan_cursor = ""
        while True:
            orphans = connection.execute(
                "SELECT f.document_id, MAX(f.name) AS name, MAX(f.path) AS path"
                " FROM documents_fts AS f"
                " WHERE f.document_id > ?"
                " AND NOT EXISTS (SELECT 1 FROM documents AS d"
                " WHERE d.id = f.document_id)"
                " GROUP BY f.document_id"
                " ORDER BY f.document_id LIMIT ?",
                (orphan_cursor, ORPHAN_CLEANUP_BATCH_SIZE),
            ).fetchall()
            if not orphans:
                break
            orphan_cursor = str(orphans[-1]["document_id"])
            orphan_ids = tuple(sorted({str(row["document_id"]) for row in orphans}))
            aliases = {}
            for row in orphans:
                orphan_id = str(row["document_id"])
                name = row["name"]
                path = row["path"]
                aliases[orphan_id] = (
                    name,
                    path,
                    Path(str(name)).stem if name else "",
                    Path(str(path)).stem if path else "",
                )
            scrub_reference_metadata(connection, orphan_ids, aliases)
            placeholders = ",".join("?" for _ in orphan_ids)
            params = orphan_ids
            connection.execute(
                f"DELETE FROM document_graph_metadata WHERE key IN ({placeholders})",
                tuple(f"dirty:{document_id}" for document_id in orphan_ids),
            )
            connection.execute(
                f"DELETE FROM document_graph_edges"
                f" WHERE source_document_id IN ({placeholders})"
                f" OR target_document_id IN ({placeholders})",
                params + params,
            )
            connection.execute(
                f"DELETE FROM document_graph_terms WHERE document_id IN ({placeholders})",
                params,
            )
            connection.execute(
                f"DELETE FROM document_graph_nodes WHERE document_id IN ({placeholders})",
                params,
            )
            connection.execute(
                f"DELETE FROM documents_fts WHERE document_id IN ({placeholders})",
                params,
            )
            removed += len(orphan_ids)

        missing_cursor = ""
        dirty_count = 0
        while True:
            missing = connection.execute(
                "SELECT id, path, name, source FROM documents AS d"
                " WHERE d.id > ?"
                " AND NOT EXISTS (SELECT 1 FROM documents_fts AS f"
                " WHERE f.document_id = d.id)"
                " ORDER BY d.id LIMIT ?",
                (missing_cursor, ORPHAN_CLEANUP_BATCH_SIZE),
            ).fetchall()
            if not missing:
                break
            missing_cursor = str(missing[-1]["id"])
            dirty_ids: list[str] = []
            for row in missing:
                if row["source"] != SourceKind.LOCAL:
                    # A cloud-only document cannot be re-read from here: skip it
                    # honestly and let the health report keep saying so.
                    skipped += 1
                    continue
                outcome = read_local_content(Path(row["path"]))
                connection.execute(
                    "INSERT INTO documents_fts(document_id,name,path,content)"
                    " VALUES (?,?,?,?)",
                    (
                        row["id"], row["name"], row["path"], outcome.text or "",
                    ),
                )
                content_hash = (
                    hashlib.sha256(outcome.text.encode("utf-8")).hexdigest()
                    if outcome.text is not None
                    else None
                )
                connection.execute(
                    "UPDATE documents SET content_hash = ? WHERE id = ?",
                    (content_hash, row["id"]),
                )
                dirty_ids.append(str(row["id"]))
                re_extracted += 1
            if dirty_ids:
                dirty_count += len(dirty_ids)
                if dirty_count > MAX_TARGETED_RECORDS:
                    mark_graph_dirty(connection, ["*"], "rebuild-fts")
                else:
                    mark_graph_dirty(connection, dirty_ids, "rebuild-fts")
        connection.commit()
    return RepairResult(
        action="rebuild-fts",
        changed=removed + re_extracted,
        skipped=skipped,
        detail=(
            f"{removed} orphaned row(s) removed, {re_extracted} document(s)"
            f" re-extracted" + (f", {skipped} skipped (not local)" if skipped else "")
        ),
    )


def re_extract(
    database: SearchDatabase, path: Path, *, confirm: bool = False
) -> RepairResult:
    """Re-read one file and refresh its document and search rows.

    Destructive: the stored text of that file is replaced, so it requires
    ``confirm=True``.
    """
    if not confirm:
        raise ConfirmationRequired(CONFIRMATION_REQUIRED)
    target = Path(path)
    if not target.exists():
        return RepairResult(
            action="re-extract", changed=0, detail=f"{target} does not exist"
        )
    document_id = document_id_for(SourceKind.LOCAL, target)
    outcome = read_local_content(target)
    with closing(database.connect()) as connection:
        exists = connection.execute(
            "SELECT 1 FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if exists is None:
            return RepairResult(
                action="re-extract", changed=0,
                detail=f"{target} is not in the index",
            )
        connection.execute(
            "UPDATE documents_fts SET name = ?, path = ?, content = ?"
            " WHERE document_id = ?",
            (target.name, str(target), outcome.text or "", document_id),
        )
        content_hash = (
            hashlib.sha256(outcome.text.encode("utf-8")).hexdigest()
            if outcome.text is not None
            else None
        )
        connection.execute(
            "UPDATE documents SET content_hash = ? WHERE id = ?",
            (content_hash, document_id),
        )
        from universal_search.intelligence.graph import mark_graph_dirty

        mark_graph_dirty(connection, [document_id], "re-extract")
        connection.commit()
    return RepairResult(
        action="re-extract", changed=1,
        detail=f"{target.name} re-read"
        + (" (extraction failed)" if outcome.error else ""),
    )


def rebuild_intelligence(
    database: SearchDatabase, *, limit: int | None = None, force: bool = False
) -> RepairResult:
    """Recompute derived metadata (phase 014). Safe: additive and versioned."""
    from universal_search.intelligence import rebuild as rebuild_analysis

    stats = rebuild_analysis(database, limit=limit, force=force)
    return RepairResult(
        action="rebuild-intelligence",
        changed=stats.updated,
        skipped=stats.skipped,
        detail=(
            f"{stats.updated} updated, {stats.skipped} unchanged,"
            f" {stats.failed} failed, {stats.removed} removed"
        ),
    )


def rebuild_all(
    database: SearchDatabase,
    roots: list[Path],
    *,
    confirm: bool = False,
    backup: Path | None = None,
) -> RepairResult:
    """Delete the whole index and index ``roots`` from scratch.

    The most destructive operation there is: every document, every search
    row and every derived row is dropped. It requires ``confirm=True`` and
    exists for the case where the index is beyond incremental repair.

    ``backup`` writes a consistent copy of the database (via SQLite's own
    backup API, so it is safe while the worker writes) before anything is
    dropped, and refuses to overwrite an existing file. This is the one
    operation where a backup is worth taking: everything it removes is
    derived, but the time to rebuild is not free.
    """
    if not confirm:
        raise ConfirmationRequired(CONFIRMATION_REQUIRED)
    path = Path(database.path)
    application_files = 0
    quarantined: list[Path] = []
    corrupt = False
    counts = {"documents": 0, "fts": 0, "intelligence": 0}
    # Probe and close before any destructive step.  A corrupt SQLite file is
    # application-owned state, not a source file; quarantine it as a whole
    # (including WAL/SHM) before opening a fresh database for counting/rebuild.
    if path.exists():
        connection = None
        try:
            connection = database.connect()
            counts = {
                "documents": int(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                ),
                "fts": int(
                    connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
                ),
                "intelligence": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM document_intelligence"
                    ).fetchone()[0]
                ),
            }
        except UnsupportedSchemaVersion:
            raise
        except sqlite3.DatabaseError:
            corrupt = True
        except (OSError, ValueError):
            raise
        finally:
            if connection is not None:
                connection.close()
    if corrupt:
        stamp = time.time_ns()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if not candidate.exists():
                continue
            quarantine = candidate.with_name(
                f"{candidate.name}.corrupt-{stamp}"
            )
            try:
                candidate.replace(quarantine)
            except PermissionError as exc:
                raise RepairBlocked(
                    f"{candidate.name} is in use — close the window and stop "
                    "the indexer, then run the rebuild again"
                ) from exc
            quarantined.append(quarantine)
            application_files += 1
        counts = {"documents": 0, "fts": 0, "intelligence": 0}
    backup_path = None
    if backup is not None and not corrupt:
        backup_path = database.backup(backup)
    # Close every connection by dropping the file itself: WAL and SHM go
    # with it, which is the only way to guarantee a clean rebuild.
    dropped = counts["documents"]
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if not candidate.exists():
            continue
        for attempt in range(5):
            try:
                candidate.unlink()
                application_files += 1
                break
            except PermissionError:
                if attempt == 4:
                    # Windows keeps the file locked while the GUI or the
                    # background worker has it open: say so instead of
                    # failing with an opaque WinError.
                    raise RepairBlocked(
                        f"{candidate.name} is in use — close the window and"
                        " stop the indexer, then run the rebuild again"
                    ) from None
                time.sleep(0.1)
    indexer = Indexer(database)
    created = 0
    for root in roots:
        created += indexer.index_root(root).created
    return RepairResult(
        action="rebuild-all",
        changed=created,
        skipped=0,
        detail=(
            f"dropped {dropped} document(s), {counts['fts']} search row(s),"
            f" {counts['intelligence']} derived row(s); reindexed {created}"
            + (
                f"; backup at {backup_path}"
                if backup_path is not None
                else " (no backup taken)"
            )
            + (
                f"; quarantined {len(quarantined)} corrupt application file(s)"
                if quarantined
                else ""
            )
        ),
        application_files=application_files,
    )


@dataclass(frozen=True, slots=True)
class SourceRemovalResult:
    """Rows removed while stopping one source; physical files are untouched."""

    path: str
    documents: int = 0
    search_rows: int = 0
    derived_rows: int = 0
    graph_rows: int = 0
    usage_rows: int = 0
    application_files: int = 0

    @property
    def source_files(self) -> int:
        return 0

    @property
    def changed(self) -> int:
        return (
            self.documents
            + self.search_rows
            + self.derived_rows
            + self.graph_rows
            + self.usage_rows
        )

    @property
    def indexed_records(self) -> int:
        return self.documents

    @property
    def physical_files(self) -> int:
        return 0

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "documents": self.documents,
            "search_rows": self.search_rows,
            "derived_rows": self.derived_rows,
            "graph_rows": self.graph_rows,
            "usage_rows": self.usage_rows,
            "application_files": self.application_files,
            "source_files": self.source_files,
            "changed": self.changed,
            "indexed_records": self.indexed_records,
            "physical_files": 0,
        }


def _path_below(path: str, root: str) -> bool:
    """Portable, case-normalized containment check for stored source paths."""
    import os

    try:
        candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
        base = os.path.normcase(os.path.abspath(os.fspath(root)))
        return os.path.commonpath((candidate, base)) == base
    except (TypeError, ValueError, OSError):
        return False


def _root_key(value: Path | str) -> str:
    import os

    return os.path.normcase(os.path.abspath(os.fspath(value))).rstrip("\\/")


def remove_indexed_source(
    database: SearchDatabase,
    root: Path | str,
    *,
    configured_roots: tuple[Path | str, ...] | list[Path | str] | None = None,
) -> SourceRemovalResult:
    """Delete canonical and derived rows below ``root`` in one transaction.

    This is deliberately an indexed-data operation, not a filesystem delete.
    No path under ``root`` is passed to ``unlink``; the caller can safely use
    this for a user's documents.  Graph metadata is scrubbed through the same
    Indexer deletion primitive used by reconciliation.
    """
    target = str(root)
    target_key = _root_key(target)
    root_specs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in (target, *(configured_roots or ())):
        key = _root_key(candidate)
        if key not in seen:
            seen.add(key)
            root_specs.append((str(candidate), key))
    if not Path(database.path).exists():
        return SourceRemovalResult(target)
    connection = database.connect()
    try:
        rows = connection.execute(
            "SELECT id, path FROM documents"
        ).fetchall()
        selected: list[tuple[str, str]] = []
        for row in rows:
            row_path = str(row["path"])
            candidates = [
                key for _display, key in root_specs if _path_below(row_path, key)
            ]
            if not candidates:
                continue
            owner = max(candidates, key=lambda value: (len(value), value))
            if owner == target_key:
                selected.append((str(row["id"]), row_path))
        search_rows = 0
        derived_rows = 0
        graph_rows = 0
        usage_rows = 0
        for document_id, _path in selected:
            search_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents_fts WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            derived_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_intelligence WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            graph_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_edges WHERE"
                    " source_document_id = ? OR target_document_id = ?",
                    (document_id, document_id),
                ).fetchone()[0]
            )
            graph_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_terms WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            graph_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_nodes WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
            usage_rows += int(
                connection.execute(
                    "SELECT COUNT(*) FROM usage_events WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            )
        for document_id, _path in selected:
            # Reuse the FK-safe canonical deletion path, including direct and
            # reverse graph-reference cleanup.  It never touches the source.
            Indexer._delete(connection, document_id)
            connection.execute(
                "DELETE FROM document_graph_metadata WHERE key = ?",
                (f"dirty:{document_id}",),
            )
            connection.execute(
                "DELETE FROM usage_events WHERE document_id = ?", (document_id,)
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return SourceRemovalResult(
        path=target,
        documents=len(selected),
        search_rows=search_rows,
        derived_rows=derived_rows,
        graph_rows=graph_rows,
        usage_rows=usage_rows,
    )
