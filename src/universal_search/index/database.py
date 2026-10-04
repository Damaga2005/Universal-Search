import sqlite3
from contextlib import closing
from pathlib import Path


class DatabaseCompactionError(RuntimeError):
    """A compact copy failed verification and was discarded (phase 047).

    A distinct type so a CLI or GUI caller can report "compaction refused, your
    index is untouched" instead of a generic failure -- the distinction is the
    whole point, because the original database is still intact when this is
    raised.
    """


class UnsupportedSchemaVersion(RuntimeError):
    """The database was written by a newer build (spec 020).

    A distinct type so the CLI, the GUI service and the diagnostics can
    explain the refusal instead of reporting a generic database error: the
    remedy is to install the newer release, not to repair the file.
    """


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT,
    modified_at TEXT,
    content_hash TEXT,
    mtime_ns INTEGER,
    last_seen_run INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'available',
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Phase 024: the canonical uniqueness contract is (source, path). Two
-- providers may own the same path (a NAS share and a local folder, a
-- removable drive and the drive letter it replaced); the provider key is
-- the source discriminator. A plain path index keeps the per-root
-- deletion range scan fast.
CREATE UNIQUE INDEX IF NOT EXISTS documents_source_path
    ON documents(source, path);
CREATE INDEX IF NOT EXISTS documents_path
    ON documents(path);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS usage_events_document
    ON usage_events(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    document_id UNINDEXED,
    name,
    path,
    content,
    tokenize = 'unicode61'
);

-- Derived, disposable document intelligence (spec 014). Search never reads
-- this table: it is rebuilt from the indexed content by
-- `universal-search intelligence rebuild`, versioned per row, and can be
-- deleted without touching the index.
CREATE TABLE IF NOT EXISTS document_intelligence (
    document_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    language TEXT,
    title TEXT,
    headings TEXT,
    terms TEXT,
    pairs TEXT,
    sections INTEGER NOT NULL DEFAULT 0,
    analyzed_chars INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Phase 025: extraction diagnostics written by the indexer at extraction
    -- time (status, sanitized warnings, truncation flag, contract version).
    -- Derived and disposable like the rest of this table: an intelligence
    -- rebuild preserves the columns, a privacy forget deletes the row.
    extraction_status TEXT,
    extraction_warnings TEXT,
    extraction_truncated INTEGER NOT NULL DEFAULT 0,
    extraction_contract INTEGER
);

CREATE INDEX IF NOT EXISTS document_intelligence_language
    ON document_intelligence(language);

-- Phase 022 derived relationship graph. These tables are local, disposable
-- and rebuilt from canonical documents; search and ranking never read them.
CREATE TABLE IF NOT EXISTS document_graph_nodes (
    document_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    source TEXT NOT NULL,
    content_hash TEXT,
    language TEXT,
    title TEXT,
    headings TEXT NOT NULL DEFAULT '[]',
    terms TEXT NOT NULL DEFAULT '[]',
    normalized_terms TEXT NOT NULL DEFAULT '[]',
    phrases TEXT NOT NULL DEFAULT '[]',
    context TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_graph_edges (
    source_document_id TEXT NOT NULL,
    target_document_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    weight REAL NOT NULL,
    evidence TEXT NOT NULL,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_document_id, target_document_id, edge_type),
    CHECK(source_document_id <> target_document_id)
);

CREATE TABLE IF NOT EXISTS document_graph_terms (
    term TEXT NOT NULL,
    document_id TEXT NOT NULL,
    weight REAL NOT NULL,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(term, document_id)
);

CREATE TABLE IF NOT EXISTS document_graph_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Phase 026 derived semantic index. Fallback-only: it is read only when
-- the lexical engine returns nothing, so exact matches, phrases, filters
-- and query operators stay authoritative. Local, versioned, rebuildable and
-- removable like the other derived data — search never requires it.
CREATE TABLE IF NOT EXISTS document_semantic (
    document_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    norm REAL NOT NULL,
    words TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_semantic_terms (
    ngram TEXT NOT NULL,
    document_id TEXT NOT NULL,
    weight REAL NOT NULL,
    idf REAL NOT NULL,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    PRIMARY KEY(ngram, document_id)
);

CREATE INDEX IF NOT EXISTS document_semantic_terms_ngram
    ON document_semantic_terms(ngram, document_id);

CREATE TABLE IF NOT EXISTS document_semantic_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS document_graph_edges_source
    ON document_graph_edges(source_document_id, weight DESC, target_document_id);
CREATE INDEX IF NOT EXISTS document_graph_edges_target
    ON document_graph_edges(target_document_id, weight DESC, source_document_id);
CREATE INDEX IF NOT EXISTS document_graph_terms_term
    ON document_graph_terms(term, document_id);
CREATE INDEX IF NOT EXISTS document_graph_terms_document
    ON document_graph_terms(document_id, term);
CREATE INDEX IF NOT EXISTS document_graph_nodes_generation
    ON document_graph_nodes(generation, version);

-- Phase 031 derived blocking fingerprints. These are NOT the match: they are
-- a cheap, bounded filter that proposes candidates for typo and prefix
-- queries, and universal_search.fuzzy/verify.py decides every match against
-- the real text. Bounded per document (at most 64 trigrams), not per byte, so
-- a huge file cannot make the index explode. Local, versioned, rebuildable
-- and removable: search never requires it.
--
-- The posting rows carry a small integer surrogate instead of the 64-character
-- document hash. Measured on the labelled corpus, storing the hash made each
-- row cost about 250 bytes and put this phase's storage gate at 35% growth;
-- with the surrogate the same rows cost about 20. The mapping is derived data
-- too, and is rebuilt with the rest.
CREATE TABLE IF NOT EXISTS document_fuzzy_documents (
    surrogate INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_fuzzy_terms (
    ngram TEXT NOT NULL,
    surrogate INTEGER NOT NULL,
    weight REAL NOT NULL,
    version INTEGER NOT NULL,
    preprocessing_version INTEGER NOT NULL,
    PRIMARY KEY(ngram, surrogate)
);

CREATE INDEX IF NOT EXISTS document_fuzzy_terms_document
    ON document_fuzzy_terms(surrogate);

CREATE TABLE IF NOT EXISTS document_fuzzy_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Forward-only migration ledger (spec 020). One row per schema version
-- this database has actually been stamped with; append-only, so it is an
-- audit trail rather than state. A database newer than this build refuses
-- to open instead of being silently downgraded.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added after the first release; applied to existing databases.
MIGRATIONS = (
    "ALTER TABLE documents ADD COLUMN mtime_ns INTEGER",
    "ALTER TABLE documents ADD COLUMN last_seen_run INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE documents ADD COLUMN availability TEXT NOT NULL DEFAULT 'available'",
    # Phase 025: extraction diagnostics live in the derived table so they
    # are explainable (diagnos*) and deletable (privacy forget) without
    # touching the canonical document row.
    "ALTER TABLE document_intelligence ADD COLUMN extraction_status TEXT",
    "ALTER TABLE document_intelligence ADD COLUMN extraction_warnings TEXT",
    "ALTER TABLE document_intelligence ADD COLUMN extraction_truncated INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE document_intelligence ADD COLUMN extraction_contract INTEGER",
)

# Phase 024: the v6 schema made `path` alone UNIQUE. SQLite cannot drop a
# column constraint in place, so the table is rebuilt: every row is copied
# into a replacement table without the constraint (same column order, so
# the copy is a plain SELECT *), the old table is dropped and the
# replacement renamed. Forward-only, idempotent (detected by the old DDL
# text) and lossless: existing local/onedrive rows keep their ids, and the
# new (source, path) unique index is created alongside.
_V7_DOCUMENTS = """
CREATE TABLE documents_v7 (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT,
    modified_at TEXT,
    content_hash TEXT,
    mtime_ns INTEGER,
    last_seen_run INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'available',
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _migrate_documents_uniqueness(connection: sqlite3.Connection) -> None:
    """v7: move the uniqueness contract from ``path`` to ``(source, path)``."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
    ).fetchone()
    if row is None or "path TEXT NOT NULL UNIQUE" not in (row["sql"] or ""):
        return  # fresh schema or already migrated
    connection.execute(_V7_DOCUMENTS)
    # Column-explicit copy: a legacy table that gained its columns through
    # ALTER TABLE has them in a different order than the replacement.
    connection.execute(
        "INSERT INTO documents_v7 (id, source, path, name, extension, size,"
        " created_at, modified_at, content_hash, mtime_ns, last_seen_run,"
        " availability, indexed_at)"
        " SELECT id, source, path, name, extension, size, created_at,"
        " modified_at, content_hash, mtime_ns, last_seen_run, availability,"
        " indexed_at FROM documents"
    )
    connection.execute("DROP TABLE documents")
    connection.execute("ALTER TABLE documents_v7 RENAME TO documents")
    connection.execute(
        "CREATE UNIQUE INDEX documents_source_path ON documents(source, path)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS documents_path ON documents(path)"
    )

# -- migration strategy ---------------------------------------------------------
# Three safe-to-repeat mechanisms guard every connection:
#   1. SCHEMA creates missing tables/indexes (fresh databases).
#   2. MIGRATIONS adds columns discovered missing by introspection, so a
#      database created before a column existed gains it without losing rows
#      (upgrades preserve the search index - spec 010).
#   3. PRAGMA user_version records the migration level actually applied, so
#      the stamp is observable by tests and tools, and future *ordered*
#      migrations have a version to step from.
# Releases bump SCHEMA_VERSION whenever SCHEMA or MIGRATIONS change
# (tests/test_release.py enforces the stamp on fresh and legacy databases).
# Adding an object to SCHEMA also upgrades existing databases: the
# schema-present gate sees a missing object and re-runs the idempotent DDL.
SCHEMA_VERSION = 10  # + derived blocking fingerprints (031)

# Every object SCHEMA creates. When all of them already exist the idempotent
# DDL is skipped: one indexed sqlite_master lookup replaces re-parsing the
# whole script on every connection (profiled as pure overhead per query).
SCHEMA_OBJECTS = (
    "documents",
    "usage_events",
    "usage_events_document",
    "documents_fts",
    "documents_source_path",
    "documents_path",
    "document_intelligence",
    "document_intelligence_language",
    "document_graph_nodes",
    "document_graph_edges",
    "document_graph_terms",
    "document_graph_metadata",
    "document_graph_edges_source",
    "document_graph_edges_target",
    "document_graph_terms_term",
    "document_graph_terms_document",
    "document_graph_nodes_generation",
    "document_semantic",
    "document_semantic_terms",
    "document_semantic_terms_ngram",
    "document_semantic_metadata",
    "document_fuzzy_documents",
    "document_fuzzy_terms",
    "document_fuzzy_terms_document",
    "document_fuzzy_metadata",
    "schema_migrations",
)

# Applied to every connection. WAL is the persistent journal mode; the rest
# is the fast path for a local, rebuildable index:
#   synchronous=NORMAL  no fsync per commit under WAL. A power failure can
#       lose only not-yet-checkpointed commits of data the filesystem can
#       rebuild anyway (profiled: 9.4ms -> ~0.05ms per commit).
#   cache_size          16 MiB of page cache per connection.
#   mmap_size           256 MiB of memory-mapped reads (skips read() calls).
#   temp_store=MEMORY   sorts and FTS temporaries never touch the disk.
#
# Phase 046 measured one more setting and rejected it, and the numbers are kept
# here so the next person does not have to earn the same answer.
#
# `wal_autocheckpoint` is never set, so SQLite uses its default of 1000 pages.
# That looked like the reason indexing was superlinear: FTS5 is ~86% of a pass
# at 10k documents (10k rows through raw SQL: 1.539 s with FTS, 0.249 s
# without), and an automatic checkpoint rewrites the WAL into the main file
# while FTS5 is mid-merge. Raising it to 20000 pages in an insert-only probe
# halved the time --
#
#     5000 documents     default 0.720 s  ->  20000 pages  0.350 s
#     10000 documents    default 1.521 s  ->  20000 pages  0.695 s
#
# -- and made no difference to the product. Measured end to end through
# `index_root`, best of three at 5000 documents:
#
#     20000 pages        51.862 s / 54.944 s / 56.423 s
#     sqlite default     50.740 s / 52.552 s / 53.557 s
#
# 0.98x, inside the run-to-run noise this machine shows. The probe was measuring
# a different workload: the indexer reads once per file *between* writes, so a
# larger WAL penalises exactly the reads the insert-only probe never performs.
#
# Two other candidates were measured and rejected the same way:
# `cache_size=-64000` gave 1.556 s against a 1.521 s default, and
# `mmap_size=1GiB` gave 1.651 s with one run at 55 s -- memory-mapping a
# multi-gigabyte working set on Windows is a trap, not a tuning knob.
#
# What that leaves is the measured shape: space is linear (3.72 -> 3.51 KiB per
# document from 1k to 10k) and peak memory is modest (2.4 -> 5.4 MiB), while
# time is dominated by FTS5's own segment merging inside SQLite. That is not a
# bottleneck this project can optimise without changing the index design, which
# phase 046 explicitly rules out. See `docs/RANKING.md` and the phase report.
_PER_CONNECTION_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA cache_size=-16000",
    "PRAGMA mmap_size=268435456",
    "PRAGMA temp_store=MEMORY",
)


class SearchDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            for pragma in _PER_CONNECTION_PRAGMAS:
                connection.execute(pragma)
            if not self._schema_present(connection):
                connection.executescript(SCHEMA)
            self._migrate(connection)
            return connection
        except BaseException:
            # A corrupt file can fail during PRAGMA/schema setup before the
            # connection is returned to the caller.  Close it here or Windows
            # will still hold the application-owned file when recovery tries to
            # quarantine it.
            connection.close()
            raise

    @staticmethod
    def _schema_present(connection: sqlite3.Connection) -> bool:
        placeholders = ",".join("?" for _ in SCHEMA_OBJECTS)
        found = connection.execute(
            f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})",
            SCHEMA_OBJECTS,
        ).fetchall()
        return len(found) == len(SCHEMA_OBJECTS)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        stored = connection.execute("PRAGMA user_version").fetchone()[0]
        if stored > SCHEMA_VERSION:
            # Downgrade refusal (spec 020): an older build must not touch a
            # database written by a newer one. Rewriting the stamp to the
            # older value would make both builds believe they own the
            # schema, and the older one can silently misread columns and
            # tables it does not know about. Refusing is the only safe
            # option; the user reinstalls the newer build.
            raise UnsupportedSchemaVersion(
                f"this index was written by a newer version of Universal "
                f"Search (schema {stored}, this build supports {SCHEMA_VERSION}). "
                f"Install the newer release, or restore a backup."
            )
        for statement in MIGRATIONS:
            # "ALTER TABLE <table> ADD COLUMN <column> ..." — the column
            # check must introspect the table the statement targets, which
            # is not always `documents` (phase 025 adds derived-table
            # columns).
            table, column = statement.split()[2], statement.split()[5]
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                connection.execute(statement)
                connection.commit()
        _migrate_documents_uniqueness(connection)
        if stored != SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            # Migration history (spec 020). Only versions this build has
            # actually stamped are recorded: inventing rows for versions we
            # did not apply would be fiction, and `diagnose` prints this
            # table, so it has to be true.
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at)"
                " VALUES (?, CURRENT_TIMESTAMP)",
                (SCHEMA_VERSION,),
            )
            connection.commit()

    def migration_history(self) -> tuple[tuple[int, str], ...]:
        """Schema versions this database has been stamped with, oldest first.

        Forward-only and append-only: upgrading the same database twice
        records one row per distinct version.
        """
        with closing(self.connect()) as connection:
            return tuple(
                (int(row["version"]), str(row["applied_at"]))
                for row in connection.execute(
                    "SELECT version, applied_at FROM schema_migrations"
                    " ORDER BY version"
                )
            )

    def sizes(self) -> dict[str, int]:
        """Byte sizes of the database files (growth monitoring, spec 011)."""
        database = _file_size(self.path)
        wal = _file_size(Path(str(self.path) + "-wal"))
        shm = _file_size(Path(str(self.path) + "-shm"))
        return {"database": database, "wal": wal, "shm": shm,
                "total": database + wal + shm}

    def backup(self, destination: Path | None = None) -> Path:
        """Copy the whole database (with WAL and SHM) to a backup file.

        Used before destructive repairs (spec 020). The copy is taken with
        SQLite's own backup API, so it is consistent even while the worker
        is writing: a plain file copy of a live WAL database is not.
        """
        target = Path(destination or Path(str(self.path) + ".backup"))
        if target.exists():
            raise FileExistsError(f"backup already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_connection = self.connect()
        try:
            backup_connection = sqlite3.connect(target)
            try:
                source_connection.backup(backup_connection)
            finally:
                backup_connection.close()
        finally:
            source_connection.close()
        return target

    def compact(self) -> dict:
        """Rewrite the database into a compact copy and swap it in, safely.

        Phase 047. ``maintenance(vacuum=True)`` could not be trusted to release
        storage, and the failure was silent in the worst way: the operation
        reported ``freelist_after=0`` and a small ``page_count`` while every
        byte stayed on disk.

        The cause was ordering, not VACUUM. In WAL mode a VACUUM writes the
        whole rewritten database into the WAL, and the main file is only
        truncated at a later checkpoint. ``maintenance`` checkpointed *before*
        vacuuming and never after, so the bytes it promised were never handed
        back.

        Measured, 1000 documents, derived layers built then purged:

            before compacting      78,782,464 bytes
            after ``maintenance``  78,782,464 bytes   (-0, freelist reported 0)
            after ``compact``       4,599,808 bytes   (-74,182,656)

        Four mechanisms were measured on copies of that same database -- raw
        VACUUM plus checkpoint, VACUUM with ``mmap_size=0``, VACUUM in DELETE
        journal mode, and ``VACUUM INTO`` -- and all four returned the bytes.
        This uses ``VACUUM INTO`` because it is the only one that produces the
        compact copy *separately*, which is what makes the swap verifiable:

          * the original is untouched until the replacement passes
            ``PRAGMA integrity_check`` and reports the same row counts;
          * an interrupted run leaves the original in place and a temporary
            file to delete, never a half-written index;
          * a failed verification raises and discards the candidate.

        Like the VACUUM it replaces, this needs an exclusive lock: run it while
        indexing is paused. It never touches the user's documents.
        """
        before = self.sizes()
        with closing(self.connect()) as connection:
            expected = self._row_counts(connection)
            freelist_before = connection.execute(
                "PRAGMA freelist_count"
            ).fetchone()[0]
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            candidate = Path(str(self.path) + ".compact")
            candidate.unlink(missing_ok=True)
            connection.execute("VACUUM INTO ?", (str(candidate),))
        try:
            with closing(sqlite3.connect(candidate)) as check:
                status = check.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                if status != "ok":
                    raise DatabaseCompactionError(
                        f"compact candidate failed integrity_check: {status}"
                    )
                found = self._row_counts(check)
            missing = {
                table: (count, found.get(table))
                for table, count in expected.items()
                if found.get(table) != count
            }
            if missing:
                raise DatabaseCompactionError(
                    "compact candidate lost rows: "
                    + ", ".join(
                        f"{table} {was} -> {now}"
                        for table, (was, now) in sorted(missing.items())
                    )
                )
            # Checkpoint first so the WAL cannot resurrect content the
            # candidate does not have, then swap and drop the sidecars: a stale
            # -wal next to a new database is how a good index becomes corrupt.
            #
            # The cursor is consumed and the connection closed explicitly
            # rather than left to a `with` block: `sqlite3.Connection.__exit__`
            # commits but does not close, and on Windows an unclosed connection
            # keeps its -wal handle open, which made the unlink below fail with
            # WinError 32 while a stray connection from *this* process was
            # still alive.
            live = sqlite3.connect(self.path)
            try:
                live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            finally:
                live.close()
            for suffix in ("-wal", "-shm"):
                _unlink_with_retry(Path(str(self.path) + suffix))
            candidate.replace(self.path)
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        after = self.sizes()
        with closing(self.connect()) as connection:
            freelist_after = connection.execute(
                "PRAGMA freelist_count"
            ).fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        result = dict(after)
        result.update(
            {
                "bytes_before": before["total"],
                "bytes_after": after["total"],
                "bytes_reclaimed": before["total"] - after["total"],
                "database_before": before["database"],
                "database_after": after["database"],
                "rows_verified": len(expected),
                "freelist_before": freelist_before,
                "freelist_after": freelist_after,
                "pages": pages,
                "page_size": page_size,
                "reclaimable_bytes": freelist_after * page_size,
            }
        )
        return result

    @staticmethod
    def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
        """Row counts of every table that has one, for swap verification."""
        counts: dict[str, int] = {}
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            try:
                counts[table] = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # FTS5 shadow tables can refuse a bare count depending on the
                # build; skipping them weakens the check but never fails it.
                continue
        return counts

    def maintenance(self, *, vacuum: bool = False) -> dict:
        """Full WAL checkpoint (optionally compacting) plus file/page sizes.

        Growth strategy from the phase-011 audit: SQLite's autocheckpoint
        already keeps the WAL steady (~4 MB measured under continuous
        indexing), so normal operation cannot grow without bound; this
        forces a TRUNCATE checkpoint on demand.

        ``vacuum=True`` used to run a bare ``VACUUM``, which on the real corpus
        reclaimed 0 of 74,956,800 bytes while reporting that it had reclaimed
        18,038 pages -- see :meth:`compact` for the measurement. It now
        delegates to it, because an operation whose whole purpose is to return
        storage may not quietly return nothing.
        """
        if vacuum:
            return self.compact()
        connection = self.connect()
        try:
            before = connection.execute("PRAGMA freelist_count").fetchone()[0]
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            after = connection.execute("PRAGMA freelist_count").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        sizes = self.sizes()
        sizes.update(
            {
                "freelist_before": before,
                "freelist_after": after,
                "pages": pages,
                "page_size": page_size,
                "reclaimable_bytes": after * page_size,
            }
        )
        return sizes


def _unlink_with_retry(path: Path, attempts: int = 10) -> None:
    """Delete a sidecar file, tolerating a transient Windows lock.

    A leftover ``-wal`` beside a freshly swapped database would be applied to
    it, so this must not be best-effort-and-hope. Windows releases the handle
    shortly after the owning connection closes, so a short bounded retry is the
    honest fix; exhausting it raises rather than leaving a stale journal.
    """
    import time as _time

    for remaining in range(attempts, 0, -1):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if remaining == 1:
                raise
            _time.sleep(0.05)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
