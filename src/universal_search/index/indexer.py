import functools
import hashlib
import json
import logging
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
from universal_search.providers.base import (
    CancelToken,
    IgnoredPath,
    ProviderError,
    ScanError,
)
from universal_search.providers.ignore import IgnoreRules
from universal_search.providers.local import read_local_content, scan_local
from universal_search.providers.onedrive import (
    AVAILABILITY_AVAILABLE,
    allow_content_read,
    availability_from_attributes,
    source_for_path,
)


ContentReader = Callable[..., ExtractionResult]
log = logging.getLogger("universal_search.indexer")

# Phase 025: extraction diagnostics are persisted in the derived
# (disposable) intelligence table, never in the canonical document row.
# A rebuild preserves these columns and a privacy forget deletes the row,
# so diagnostics are explainable without outliving the document.
_EXTRACTION_DIAGNOSTICS_SQL = """
INSERT INTO document_intelligence (
    document_id, version, extraction_status, extraction_warnings,
    extraction_truncated, extraction_contract
) VALUES (?, 0, ?, ?, ?, ?)
ON CONFLICT(document_id) DO UPDATE SET
    extraction_status=excluded.extraction_status,
    extraction_warnings=excluded.extraction_warnings,
    extraction_truncated=excluded.extraction_truncated,
    extraction_contract=excluded.extraction_contract
"""

# Stored warnings are bounded twice: the extractor sanitizes them, and the
# indexer caps the list and each entry again before JSON-encoding.
MAX_STORED_WARNINGS = 20
MAX_STORED_WARNING_CHARS = 300

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
    # Enumeration errors a provider bounded away (beyond MAX_PROVIDER_ERRORS):
    # surfaced here instead of being silently dropped.
    dropped_errors: int = 0

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
            "dropped_errors",
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
            "dropped_errors": self.dropped_errors,
        }

    def summary(self) -> str:
        return (
            f"created={self.created} updated={self.updated} "
            f"unchanged={self.unchanged} deleted={self.deleted} "
            f"ignored={self.ignored} errors={self.errors} "
            f"extraction_errors={self.extraction_errors} "
            f"cloud_only={self.cloud_only} "
            f"dropped_errors={self.dropped_errors}"
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

    def _invalidate_graph(self, document_ids: set[str]) -> None:
        """Refresh optional derived edges without making indexing depend on them."""
        try:
            from universal_search.intelligence.graph import GraphStore

            GraphStore(self.database).invalidate(document_ids)
        except Exception:
            # The dirty marker was committed with the canonical write.  A
            # later related() lookup repairs this state; indexing itself must
            # not fail because an optional derived layer is unavailable.
            log.exception("graph invalidation deferred to dirty-marker repair")
            return

    @staticmethod
    def _mark_graph_dirty(connection, document_ids: set[str]) -> None:
        if not document_ids:
            return
        from universal_search.intelligence.graph import mark_graph_dirty

        mark_graph_dirty(connection, document_ids)

    # -- single document ---------------------------------------------------

    def upsert(self, document: Document) -> None:
        connection = self.connection()
        # Phase 024: identity is (source, path) — the same path owned by two
        # providers is two documents, so the previous-row lookup is scoped
        # by source as well.
        previous = connection.execute(
            "SELECT id FROM documents WHERE path = ? AND source = ?",
            (str(document.path), str(document.source)),
        ).fetchone()
        touched = {document.id}
        if previous is not None:
            touched.add(str(previous["id"]))
        try:
            self._upsert(connection, document)
            self._mark_graph_dirty(connection, touched)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        self._invalidate_graph(touched)

    @staticmethod
    def _upsert(
        connection,
        document: Document,
        mtime_ns: int | None = None,
        run_id: int | None = None,
        availability: str = AVAILABILITY_AVAILABLE,
    ) -> None:
        previous = connection.execute(
            "SELECT id, name, path FROM documents WHERE path = ? AND source = ?",
            (str(document.path), str(document.source)),
        ).fetchone()
        stale_ids = {document.id}
        if previous is not None:
            stale_ids.add(previous["id"])
            if previous["id"] != document.id:
                from universal_search.intelligence.graph import (
                    scrub_reference_metadata,
                )

                scrub_reference_metadata(
                    connection,
                    [previous["id"]],
                    {previous["id"]: (previous["name"], previous["path"])},
                )
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
            ON CONFLICT(source, path) DO UPDATE SET
                id=excluded.id, source=excluded.source, name=excluded.name,
                extension=excluded.extension, size=excluded.size,
                created_at=excluded.created_at, modified_at=excluded.modified_at,
                content_hash=excluded.content_hash, mtime_ns=excluded.mtime_ns,
                last_seen_run=excluded.last_seen_run,
                availability=excluded.availability,
                indexed_at=CURRENT_TIMESTAMP
        """, (
            document.id, str(document.source), str(document.path), document.name,
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
    def _record_extraction(connection, document_id: str, outcome: ExtractionResult) -> None:
        """Persist extraction status/warnings/truncation beside the document.

        Only the extraction columns are written; an intelligence rebuild
        upserts its own analysis columns into the same row and never
        touches these. Documents skipped as unchanged keep the diagnostics
        of their previous extraction — the content is byte-identical, so
        the extraction outcome is too.
        """
        warnings = [
            str(warning)[:MAX_STORED_WARNING_CHARS]
            for warning in outcome.warnings[:MAX_STORED_WARNINGS]
        ]
        connection.execute(
            _EXTRACTION_DIAGNOSTICS_SQL,
            (
                document_id,
                outcome.status,
                json.dumps(warnings, ensure_ascii=False),
                1 if outcome.truncated else 0,
                outcome.contract_version,
            ),
        )

    @staticmethod
    def _delete(connection, document_id: str) -> None:
        from universal_search.intelligence.graph import scrub_reference_metadata

        row = connection.execute(
            "SELECT name, path FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        aliases = (
            {document_id: (row["name"], row["path"])} if row is not None else None
        )
        scrub_reference_metadata(connection, [document_id], aliases)
        Indexer._mark_graph_dirty(connection, {document_id})
        connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (document_id,))
        # Derived document intelligence follows the canonical document.  Keeping
        # it after a source removal leaves a stale privacy/control-center row
        # and makes derived counts disagree with the index.
        connection.execute(
            "DELETE FROM document_intelligence WHERE document_id = ?",
            (document_id,),
        )
        # Graph rows are derived and must disappear with their canonical
        # document.  The explicit deletes keep cleanup correct for legacy
        # databases created before graph foreign keys/triggers existed.
        connection.execute(
            "DELETE FROM document_graph_edges WHERE source_document_id = ?"
            " OR target_document_id = ?",
            (document_id, document_id),
        )
        connection.execute(
            "DELETE FROM document_graph_terms WHERE document_id = ?", (document_id,)
        )
        connection.execute(
            "DELETE FROM document_graph_nodes WHERE document_id = ?", (document_id,)
        )
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
        provider=None,
        cancel: CancelToken | None = None,
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

        Without ``provider`` the pass classifies each file from its path
        (OneDrive roots are detected), which is the phase-023 behaviour the
        CLI, the worker and the control centre keep using. With ``provider``
        the pass enumerates through ``provider.iter_files`` instead and every
        file is owned by that provider (``documents.source`` is the provider
        key), so two providers can own the same root; the deletion pass is
        then scoped to the provider's own rows. A provider that dies
        mid-enumeration costs only its own pass: the error is counted and
        the deletion pass is skipped, because files after the failure point
        were never seen and would otherwise be deleted from the index while
        still existing on disk.
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
            graph_touched: set[str] = set()
            enumeration_failed = False
            items = (
                scan_local(root_path, rules)
                if provider is None
                else provider.iter_files(root_path, cancel)
            )
            try:
                for item in items:
                    if isinstance(item, IgnoredPath):
                        stats.ignored += 1
                        continue
                    if isinstance(item, (ProviderError, ScanError)):
                        stats.errors += 1
                        continue
                    if provider is None:
                        source_value: str = source_for_path(item.path).value
                        availability = availability_from_attributes(item.attributes)
                    else:
                        source_value = str(getattr(provider, "key", ""))
                        availability = item.availability or AVAILABILITY_AVAILABLE
                    if str(item.path) in own_files:
                        stats.ignored += 1
                        continue

                    if provider is None:
                        stored = connection.execute(
                            "SELECT id, size, mtime_ns, content_hash, source, "
                            "availability FROM documents WHERE path = ?",
                            (str(item.path),),
                        ).fetchone()
                    else:
                        # Two providers may own the same path: scope the
                        # lookup by source so one provider never reads or
                        # re-extracts another provider's row.
                        stored = connection.execute(
                            "SELECT id, size, mtime_ns, content_hash, source, "
                            "availability FROM documents WHERE path = ? AND source = ?",
                            (str(item.path), source_value),
                        ).fetchone()
                    if (
                        stored is not None
                        and stored["size"] == item.size
                        and stored["mtime_ns"] == item.mtime_ns
                        and stored["source"] == source_value
                        and stored["availability"] == availability
                    ):
                        stats.unchanged += 1
                        connection.execute(
                            "UPDATE documents SET last_seen_run = ? WHERE id = ?",
                            (run_id, stored["id"]),
                        )
                        pending += 1
                        if pending >= COMMIT_EVERY:
                            self._mark_graph_dirty(connection, graph_touched)
                            connection.commit()
                            pending = 0
                        continue

                    content: str | None = None
                    outcome: ExtractionResult | None = None
                    if allow_content_read(availability, item.size, onedrive_download_mb):
                        try:
                            if cancel is not None:
                                # Forward the cooperative cancel so a long
                                # extraction can be stopped mid-document.
                                reader = functools.partial(
                                    read_content, cancel=cancel.cancelled
                                )
                                outcome = reader(item.path)
                            else:
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
                        id=document_id_for(source_value, item.path),
                        source=source_value,
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
                        if outcome is not None:
                            self._record_extraction(connection, document.id, outcome)
                        stats.created += 1
                        graph_touched.add(document.id)
                    elif (
                        content is not None
                        and stored["content_hash"] is not None
                        and document.content_hash == stored["content_hash"]
                        and stored["source"] == source_value
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
                        if outcome is not None:
                            self._record_extraction(connection, document.id, outcome)
                        stats.updated += 1
                        graph_touched.add(document.id)
                        if stored["id"] != document.id:
                            graph_touched.add(str(stored["id"]))
                    pending += 1
                    if pending >= COMMIT_EVERY:
                        self._mark_graph_dirty(connection, graph_touched)
                        connection.commit()
                        pending = 0
                    if delay:
                        time.sleep(delay)
            except Exception:
                if provider is None:
                    # Legacy classification path: a crash propagates exactly
                    # as it always has — the caller (CLI, worker, control
                    # centre) records the failure, the committed batches stay
                    # durable and the open transaction rolls back on close.
                    raise
                # A provider that dies mid-enumeration (a share that hangs,
                # a drive that was pulled) costs only its own pass. The
                # deletion pass is skipped: files after the failure point
                # were never seen, and deleting them would remove documents
                # that still exist on disk.
                log.exception("enumeration failed for root %s", root_path)
                stats.errors += 1
                enumeration_failed = True

            if cancel is not None and cancel.cancelled:
                # A cooperative cancel means the pass did not see the whole
                # tree; treat it as incomplete so the deletion pass is
                # skipped (it would delete rows never reached).
                enumeration_failed = True

            total_errors = getattr(provider, "_enumeration_errors", None)
            if total_errors is not None and total_errors > stats.errors:
                # The provider bounded its error report; surface the errors
                # it dropped instead of silently discarding them.
                stats.dropped_errors += total_errors - stats.errors

            if not enumeration_failed:
                self._delete_missing(
                    connection, root_path, run_id, stats,
                    source=None if provider is None else str(getattr(provider, "key", "")),
                )
            self._mark_graph_dirty(connection, graph_touched)
            connection.commit()
            db_writes = connection.total_changes - writes_before
        if graph_touched:
            self._invalidate_graph(graph_touched)
        if stats.created or stats.updated or stats.deleted:
            self._mark_semantic_dirty()
        record_index(time.perf_counter() - started, stats.as_dict(), db_writes)
        return stats

    def _mark_semantic_dirty(self) -> None:
        """Mark the derived semantic index stale after a pass (phase 026).

        The semantic index is fallback-only derived data, so a pass that
        changed documents only needs to flag it — the index rebuilds lazily
        on the next fallback search. A failure here must never fail the
        indexing pass, exactly like graph invalidation.
        """
        try:
            from universal_search.semantic.index import SemanticIndex

            SemanticIndex(self.database).mark_dirty()
        except Exception:
            log.exception("semantic index marked dirty deferred to rebuild")
            return

    def index_sources(
        self,
        sources,
        *,
        rules: IgnoreRules | None = None,
        read_content: ContentReader | None = None,
        delay: float = 0.0,
        onedrive_download_mb: float = 0.0,
        cancel: CancelToken | None = None,
    ) -> IndexStats:
        """Index several ``(provider, root)`` pairs with failure isolation.

        Each pair is one reconciliation pass with its own bounded
        transactions; a provider that raises costs only its own pass (the
        error is counted and the next provider is still indexed), and a
        cancelled token stops the loop between providers.
        """
        totals = IndexStats()
        for provider, root in sources:
            if cancel is not None and cancel.cancelled:
                break
            try:
                totals.merge(
                    self.index_root(
                        root,
                        rules=rules,
                        read_content=read_content,
                        delay=delay,
                        onedrive_download_mb=onedrive_download_mb,
                        provider=provider,
                        cancel=cancel,
                    )
                )
            except Exception:
                log.exception(
                    "provider %s failed on %s",
                    getattr(provider, "key", provider),
                    root,
                )
                totals.errors += 1
        return totals

    @staticmethod
    def _delete_missing(
        connection,
        root_path: Path,
        run_id: int,
        stats: IndexStats,
        source: str | None = None,
    ) -> None:
        root_text = str(root_path)
        if source is None:
            # Legacy classification: a path belongs to exactly one source,
            # so the path range covers every row under the root.
            stale = connection.execute(
                """SELECT id FROM documents
                   WHERE path > ? AND path < ? AND last_seen_run < ?""",
                (root_text + os.sep, root_text + "\uffff", run_id),
            ).fetchall()
        else:
            # Phase 024: two providers may own the same path, so the range
            # is scoped to this provider's own rows.
            stale = connection.execute(
                """SELECT id FROM documents
                   WHERE source = ? AND path > ? AND path < ? AND last_seen_run < ?""",
                (source, root_text + os.sep, root_text + "\uffff", run_id),
            ).fetchall()
        for row in stale:
            Indexer._delete(connection, row["id"])
        stats.deleted += len(stale)


def _own_database_files(path: Path) -> set[str]:
    base = str(path)
    return {base, base + "-wal", base + "-shm"}
