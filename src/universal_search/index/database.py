import sqlite3
from contextlib import closing
from pathlib import Path


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
    path TEXT NOT NULL UNIQUE,
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
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS document_intelligence_language
    ON document_intelligence(language);

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
SCHEMA_VERSION = 5  # + schema_migrations ledger and downgrade refusal (020)

# Every object SCHEMA creates. When all of them already exist the idempotent
# DDL is skipped: one indexed sqlite_master lookup replaces re-parsing the
# whole script on every connection (profiled as pure overhead per query).
SCHEMA_OBJECTS = (
    "documents",
    "usage_events",
    "usage_events_document",
    "documents_fts",
    "document_intelligence",
    "document_intelligence_language",
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
        connection.row_factory = sqlite3.Row
        for pragma in _PER_CONNECTION_PRAGMAS:
            connection.execute(pragma)
        if not self._schema_present(connection):
            connection.executescript(SCHEMA)
        self._migrate(connection)
        return connection

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
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
        for statement in MIGRATIONS:
            column = statement.split()[5]
            if column not in columns:
                connection.execute(statement)
                columns.add(column)
                connection.commit()
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

    def maintenance(self, *, vacuum: bool = False) -> dict:
        """Full WAL checkpoint (optionally VACUUM) plus file/page sizes.

        Growth strategy from the phase-011 audit: SQLite's autocheckpoint
        already keeps the WAL steady (~4 MB measured under continuous
        indexing), so normal operation cannot grow without bound; this
        forces a TRUNCATE checkpoint on demand. ``vacuum=True`` reclaims
        fragmented free pages by rewriting the file — it needs an
        exclusive lock, so run it while indexing is paused. Returns the
        sizes plus freelist page counts before/after.
        """
        connection = self.connect()
        try:
            before = connection.execute("PRAGMA freelist_count").fetchone()[0]
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if vacuum:
                connection.execute("VACUUM")
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
            }
        )
        return sizes


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
