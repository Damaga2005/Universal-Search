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
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from universal_search.domain.document import SourceKind, document_id_for
from universal_search.index.database import SearchDatabase
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

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "changed": self.changed,
            "skipped": self.skipped,
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
    backup_path = database.backup(backup) if backup is not None else None
    # Explicit close: sqlite3's `with connection` commits but does not
    # close, and Windows refuses to delete a file that is still open.
    connection = database.connect()
    try:
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
    finally:
        connection.close()
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
        ),
    )
