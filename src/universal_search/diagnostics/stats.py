"""Index statistics: what is in the database, and how big (spec 015).

Read-only and total: every number here answers a question a user asks
before trusting a search ("how many documents? how big? when was the
last pass? is the worker running?"). Nothing in this module writes, and
nothing reads document content — counts, sizes and metadata only.
"""

from contextlib import closing
from dataclasses import dataclass, replace
import os
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


# -- control-center snapshots -------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceStatistics:
    """Bounded metadata for one configured source root.

    The object intentionally contains no document text.  Counts are aggregate
    values and ``stale_documents`` is an existence check over the rows that
    belong to this root; the control center can therefore explain why a
    source looks stale without opening a file.
    """

    path: str
    documents: int = 0
    indexed_bytes: int = 0
    by_type: tuple[tuple[str, int], ...] = ()
    by_source: tuple[tuple[str, int], ...] = ()
    stale_documents: int = 0
    accessible: bool = True
    available: bool = True
    error: str | None = None

    @property
    def count(self) -> int:
        return self.documents

    @property
    def supported_types(self) -> tuple[tuple[str, int], ...]:
        return self.by_type

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "documents": self.documents,
            "count": self.documents,
            "indexed_bytes": self.indexed_bytes,
            "by_type": dict(self.by_type),
            "by_source": dict(self.by_source),
            "supported_types": dict(self.by_type),
            "stale_documents": self.stale_documents,
            "accessible": self.accessible,
            "available": self.available,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class DerivedStatistics:
    """Version/state summary for optional derived layers."""

    intelligence_rows: int = 0
    intelligence_version: int | None = None
    graph_nodes: int = 0
    graph_edges: int = 0
    graph_terms: int = 0
    graph_metadata: int = 0
    graph_version: int | None = None
    graph_preprocessing_version: int | None = None
    graph_current: bool = False
    dirty: int = 0
    error: str | None = None

    @property
    def relationships(self) -> int:
        return self.graph_edges

    @property
    def ready(self) -> bool:
        return self.error is None and self.graph_current and self.dirty == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "intelligence_rows": self.intelligence_rows,
            "intelligence_version": self.intelligence_version,
            "graph_nodes": self.graph_nodes,
            "graph_edges": self.graph_edges,
            "graph_terms": self.graph_terms,
            "graph_metadata": self.graph_metadata,
            "graph_version": self.graph_version,
            "graph_preprocessing_version": self.graph_preprocessing_version,
            "graph_current": self.graph_current,
            "dirty": self.dirty,
            "ready": self.ready,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class StorageStatistics:
    """Measured bytes for the index and nearby local operational files."""

    database_bytes: int = 0
    wal_bytes: int = 0
    shm_bytes: int = 0
    application_files: int = 0
    source_files: int = 0
    config_bytes: int = 0
    log_bytes: int = 0
    metrics_bytes: int = 0
    control_state_bytes: int = 0
    error: str | None = None

    @property
    def total_bytes(self) -> int:
        return self.database_bytes + self.wal_bytes + self.shm_bytes

    @property
    def application_bytes(self) -> int:
        return (
            self.total_bytes
            + self.config_bytes
            + self.log_bytes
            + self.metrics_bytes
            + self.control_state_bytes
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "database": self.database_bytes,
            "wal": self.wal_bytes,
            "shm": self.shm_bytes,
            "total": self.total_bytes,
            "application_files": self.application_files,
            "source_files": self.source_files,
            "config": self.config_bytes,
            "log": self.log_bytes,
            "metrics": self.metrics_bytes,
            "control_state": self.control_state_bytes,
            "application": self.application_bytes,
            "error": self.error,
        }


def _normalised_path(value: str | Path) -> str:
    text = os.path.normcase(os.path.abspath(os.fspath(value)))
    return text.rstrip("\\/") or os.path.sep


def _under(path: str, root: str) -> bool:
    candidate = _normalised_path(path)
    base = _normalised_path(root)
    try:
        return os.path.commonpath((candidate, base)) == base
    except (ValueError, OSError):
        return False


def collect_sources(
    database: SearchDatabase,
    roots: tuple[str | Path, ...] | list[str | Path],
    *,
    stale_limit: int | None = None,
) -> tuple[SourceStatistics, ...]:
    """Collect per-source counts without opening or reading document content.

    A root that is missing or unreadable is represented as an inaccessible
    source; a damaged database is represented by zero counts plus ``error``
    rather than an exception.  ``stale_limit`` is an optional safety valve
    for callers that only need a sample; the control center leaves it unset so
    its displayed source count is exact for the rows in the index.
    """
    selected: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for raw_root in roots:
        display = str(raw_root)
        key = _normalised_path(display)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append((display, key))
    if not selected:
        return ()
    rows: list[tuple[str, int, str, str]] = []
    database_error: str | None = None
    if Path(database.path).exists():
        try:
            with closing(database.connect()) as connection:
                rows = [
                    (
                        str(row["path"]),
                        int(row["size"] or 0),
                        str(row["extension"] or ""),
                        str(row["source"] or ""),
                    )
                    for row in connection.execute(
                        "SELECT path, size, extension, source FROM documents"
                    )
                ]
        except Exception as exc:
            database_error = f"{type(exc).__name__}: {exc}"
            rows = []
    # A document belongs to the most-specific configured root.  This keeps a
    # configured parent from double-counting a configured child and gives
    # source removal the same ownership rule as the presentation layer.
    owned: dict[str, list[tuple[str, int, str, str]]] = {
        key: [] for _display, key in selected
    }
    for row in rows:
        candidates = [key for _display, key in selected if _under(row[0], key)]
        if candidates:
            owner = max(candidates, key=lambda value: (len(value), value))
            owned[owner].append(row)
    result: list[SourceStatistics] = []
    for root, root_key in selected:
        root_path = Path(root)
        accessible = True
        error = database_error
        try:
            exists = root_path.exists()
            is_directory = root_path.is_dir()
        except OSError as exc:
            exists = False
            is_directory = False
            error = f"{type(exc).__name__}: {exc}"
        if not exists or not is_directory:
            accessible = False
            error = error or "la carpeta no existe o no es accesible"
        matches = owned[root_key]
        stale_rows = matches
        if stale_limit is not None:
            stale_rows = matches[: max(0, int(stale_limit))]
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        stale = 0
        indexed_bytes = 0
        for path, size, extension, source in matches:
            indexed_bytes += max(0, size)
            by_type[extension or "(none)"] = by_type.get(extension or "(none)", 0) + 1
            by_source[source or "(none)"] = by_source.get(source or "(none)", 0) + 1
        for path, _size, _extension, _source in stale_rows:
            try:
                if not os.path.exists(path):
                    stale += 1
            except OSError:
                stale += 1
        result.append(
            SourceStatistics(
                path=str(root),
                documents=len(matches),
                indexed_bytes=indexed_bytes,
                by_type=tuple(
                    sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
                ),
                by_source=tuple(
                    sorted(by_source.items(), key=lambda item: (-item[1], item[0]))
                ),
                stale_documents=stale,
                accessible=accessible,
                available=accessible,
                error=error,
            )
        )
    return tuple(result)


def collect_derived(database: SearchDatabase) -> DerivedStatistics:
    """Read the optional intelligence/graph state without repairing it."""
    from universal_search.intelligence.graph import (
        GRAPH_PREPROCESSING_VERSION,
        GRAPH_SCHEMA_VERSION,
    )

    values = DerivedStatistics()
    if not Path(database.path).exists():
        return values
    try:
        with closing(database.connect()) as connection:
            intelligence = connection.execute(
                "SELECT COUNT(*), MIN(version), MAX(version) FROM document_intelligence"
            ).fetchone()
            graph_nodes = int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_nodes"
                ).fetchone()[0]
            )
            graph_edges = int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_edges"
                ).fetchone()[0]
            )
            graph_terms = int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_terms"
                ).fetchone()[0]
            )
            metadata_rows = connection.execute(
                "SELECT key, value FROM document_graph_metadata"
            ).fetchall()
            metadata = {str(row["key"]): str(row["value"]) for row in metadata_rows}
            dirty = int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_graph_metadata WHERE key LIKE 'dirty:%'"
                ).fetchone()[0]
            )
    except Exception as exc:
        return DerivedStatistics(error=f"{type(exc).__name__}: {exc}")
    try:
        graph_version = int(metadata["version"]) if "version" in metadata else None
    except (TypeError, ValueError):
        graph_version = None
    try:
        graph_preprocessing_version = (
            int(metadata["preprocessing_version"])
            if "preprocessing_version" in metadata
            else None
        )
    except (TypeError, ValueError):
        graph_preprocessing_version = None
    intelligence_min = intelligence[1]
    intelligence_max = intelligence[2]
    return DerivedStatistics(
        intelligence_rows=int(intelligence[0] or 0),
        intelligence_version=(
            int(intelligence_min)
            if intelligence_min is not None and intelligence_max == intelligence_min
            else None
        ),
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        graph_terms=graph_terms,
        graph_metadata=len(metadata),
        graph_version=graph_version,
        graph_preprocessing_version=graph_preprocessing_version,
        graph_current=(
            graph_version == GRAPH_SCHEMA_VERSION
            and graph_preprocessing_version == GRAPH_PREPROCESSING_VERSION
            and dirty == 0
        ),
        dirty=dirty,
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect_storage(
    database: SearchDatabase, paths: AppPaths | None = None
) -> StorageStatistics:
    """Measure index/application storage without reading file contents."""
    try:
        sizes = database.sizes()
    except Exception as exc:
        return StorageStatistics(error=f"{type(exc).__name__}: {exc}")
    home = paths.home if paths is not None else Path(database.path).parent
    return StorageStatistics(
        database_bytes=int(sizes.get("database", 0)),
        wal_bytes=int(sizes.get("wal", 0)),
        shm_bytes=int(sizes.get("shm", 0)),
        application_files=sum(
            1
            for candidate in (Path(database.path), Path(str(database.path) + "-wal"), Path(str(database.path) + "-shm"))
            if candidate.exists()
        ),
        source_files=0,
        config_bytes=_file_size(home / "config.json"),
        log_bytes=_file_size(home / "universal-search.log"),
        metrics_bytes=_file_size(home / "metrics.jsonl"),
        control_state_bytes=_file_size(home / "control-center.json"),
    )
