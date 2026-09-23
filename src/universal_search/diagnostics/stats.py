"""Index statistics: what is in the database, and how big (spec 015).

Read-only and total: every number here answers a question a user asks
before trusting a search ("how many documents? how big? when was the
last pass? is the worker running?"). Nothing in this module writes, and
nothing reads document content — counts, sizes and metadata only.
"""

from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from universal_search import __version__, metrics
from universal_search.appconfig import AppPaths
from universal_search.index.database import (
    SCHEMA_VERSION,
    SearchDatabase,
    UnsupportedSchemaVersion,
)

# How many stale-document paths a report may name. A diagnostic that
# prints 400 000 paths is a denial of service against the reader.
SAMPLE_LIMIT = 5


@dataclass(frozen=True, slots=True)
class IndexStatistics:
    """One coherent snapshot of the index. Every field is cheap to get."""

    database: Path
    exists: bool
    schema_version: int
    documents: int
    with_content: int
    cloud_only: int
    by_type: tuple[tuple[str, int], ...] = ()
    by_source: tuple[tuple[str, int], ...] = ()
    database_bytes: int = 0
    wal_bytes: int = 0
    shm_bytes: int = 0
    intelligence_rows: int = 0
    usage_events: int = 0
    migration_history: tuple[tuple[int, str], ...] = ()
    last_index_pass: dict | None = None
    worker_state: str | None = None
    worker_updated_at: str | None = None
    worker_error: str | None = None
    worker_roots: int | None = None
    lock_pid: int | None = None
    app_version: str = __version__
    error: str | None = None

    @property
    def total_bytes(self) -> int:
        return self.database_bytes + self.wal_bytes + self.shm_bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database),
            "exists": self.exists,
            "error": self.error,
            "schema_version": self.schema_version,
            "expected_schema_version": SCHEMA_VERSION,
            "documents": self.documents,
            "with_content": self.with_content,
            "cloud_only": self.cloud_only,
            "by_type": dict(self.by_type),
            "by_source": dict(self.by_source),
            "sizes": {
                "database": self.database_bytes,
                "wal": self.wal_bytes,
                "shm": self.shm_bytes,
                "total": self.total_bytes,
            },
            "intelligence_rows": self.intelligence_rows,
            "usage_events": self.usage_events,
            "migration_history": [
                {"version": version, "applied_at": applied_at}
                for version, applied_at in self.migration_history
            ],
            "last_index_pass": self.last_index_pass,
            "worker": {
                "state": self.worker_state,
                "updated_at": self.worker_updated_at,
                "error": self.worker_error,
                "roots": self.worker_roots,
                "lock_pid": self.lock_pid,
            },
            "app_version": self.app_version,
        }


def _pairs(connection, sql: str) -> tuple[tuple[str, int], ...]:
    return tuple((str(row[0]), int(row[1])) for row in connection.execute(sql))


def _last_index_pass() -> dict | None:
    """Most recent index pass from the local metrics ring (phase 011)."""
    for record in reversed(metrics.records()):
        if record.get("kind") == "index":
            return {
                key: record.get(key)
                for key in ("duration_s", "db_writes", "stats", "at")
                if key in record
            }
    return None


def collect(
    database: SearchDatabase, paths: AppPaths | None = None
) -> IndexStatistics:
    """Snapshot of the index, the database file and the worker state."""
    from universal_search import background

    database_path = Path(database.path)
    if not database_path.exists():
        return IndexStatistics(
            database=database_path,
            exists=False,
            schema_version=0,
            documents=0,
            with_content=0,
            cloud_only=0,
            error="the database file does not exist yet",
        )
    sizes = database.sizes()
    snapshot = IndexStatistics(
        database=database_path,
        exists=True,
        schema_version=0,
        documents=0,
        with_content=0,
        cloud_only=0,
        database_bytes=sizes["database"],
        wal_bytes=sizes["wal"],
        shm_bytes=sizes["shm"],
        last_index_pass=_last_index_pass(),
    )
    try:
        with closing(database.connect()) as connection:
            snapshot = replace(
                snapshot,
                schema_version=int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                ),
                documents=int(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                ),
                with_content=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents_fts WHERE content <> ''"
                    ).fetchone()[0]
                ),
                cloud_only=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents"
                        " WHERE availability = 'cloud-only'"
                    ).fetchone()[0]
                ),
                by_type=_pairs(
                    connection,
                    "SELECT extension, COUNT(*) FROM documents"
                    " GROUP BY extension ORDER BY COUNT(*) DESC, extension",
                ),
                by_source=_pairs(
                    connection,
                    "SELECT source, COUNT(*) FROM documents"
                    " GROUP BY source ORDER BY COUNT(*) DESC, source",
                ),
                intelligence_rows=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM document_intelligence"
                    ).fetchone()[0]
                ),
                usage_events=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM usage_events"
                    ).fetchone()[0]
                ),
                migration_history=tuple(
                    (int(row["version"]), str(row["applied_at"]))
                    for row in connection.execute(
                        "SELECT version, applied_at FROM schema_migrations"
                        " ORDER BY version"
                    )
                ),
            )
    except UnsupportedSchemaVersion as exc:
        # A newer build owns this index; report the fact, never a count
        # computed against a schema this build does not understand.
        return replace(snapshot, error=str(exc))
    except Exception as exc:
        # A corrupt database must still be reportable, not raise: the whole
        # point of a diagnostic is to describe the damaged state.
        return replace(
            snapshot, error=f"{type(exc).__name__}: {exc}"
        )
    if paths is not None:
        status = background.read_status(paths) or {}
        snapshot = replace(
            snapshot,
            worker_state=status.get("state"),
            worker_updated_at=status.get("updated_at"),
            worker_error=status.get("error"),
            worker_roots=status.get("roots"),
            lock_pid=background.read_lock_pid(paths),
        )
    return snapshot
