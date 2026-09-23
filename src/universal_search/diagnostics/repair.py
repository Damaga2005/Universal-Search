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

import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from universal_search.domain.document import SourceKind, document_id_for
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.providers.local import read_local_content

CONFIRMATION_REQUIRED = "this operation deletes data; pass confirm=True"


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
        orphans = connection.execute(
            "SELECT document_id FROM documents_fts AS f"
            " WHERE NOT EXISTS (SELECT 1 FROM documents AS d"
            " WHERE d.id = f.document_id)"
        ).fetchall()
        connection.executemany(
            "DELETE FROM documents_fts WHERE document_id = ?",
            [(row["document_id"],) for row in orphans],
        )
        removed += len(orphans)
        missing = connection.execute(
            "SELECT id, path, name, source FROM documents AS d"
            " WHERE NOT EXISTS (SELECT 1 FROM documents_fts AS f"
            " WHERE f.document_id = d.id)"
        ).fetchall()
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
            re_extracted += 1
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
    database: SearchDatabase, roots: list[Path], *, confirm: bool = False
) -> RepairResult:
    """Delete the whole index and index ``roots`` from scratch.

    The most destructive operation there is: every document, every search
    row and every derived row is dropped. It requires ``confirm=True`` and
    exists for the case where the index is beyond incremental repair.
    """
    if not confirm:
        raise ConfirmationRequired(CONFIRMATION_REQUIRED)
    path = Path(database.path)
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
        ),
    )
