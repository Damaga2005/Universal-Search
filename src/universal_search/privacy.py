"""What is stored, why, and how to make it go away (spec 018).

The application indexes private material, so the honest way to talk about
privacy is to enumerate every byte it keeps. :data:`INVENTORY` is that
enumeration, as data, so it cannot drift from reality unnoticed: a test
asserts every declared item has a location, a purpose, a retention and a
deletion path, and :func:`inventory_report` measures the real sizes.

Two rules hold for every item below:

1. **Nothing leaves the machine.** There is no network code in this
   application; "leaves_machine" is False everywhere and the test proves
   it by construction rather than by promise.
2. **Everything is deletable** without reinstalling, either item by item
   (:func:`forget`, ``usage clear``, ``intelligence clear``) or wholesale
   (``diagnose repair all``).
"""

from dataclasses import dataclass
from pathlib import Path

from universal_search.appconfig import AppPaths
from universal_search.index.database import SearchDatabase


@dataclass(frozen=True, slots=True)
class DataItem:
    """One category of stored data and its lifecycle."""

    key: str
    what: str
    where: str
    purpose: str
    retention: str
    deletion: str
    leaves_machine: bool = False
    optional: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "what": self.what,
            "where": self.where,
            "purpose": self.purpose,
            "retention": self.retention,
            "deletion": self.deletion,
            "leaves_machine": self.leaves_machine,
            "optional": self.optional,
        }


INVENTORY: tuple[DataItem, ...] = (
    DataItem(
        key="documents",
        what="path, name, size, timestamps, content hash",
        where="SQLite table `documents` in the index database",
        purpose="know what to index, detect changes, open the file",
        retention="until the file disappears or `forget`/rebuild removes it",
        deletion="`universal-search privacy forget <path>`, `diagnose repair all`",
    ),
    DataItem(
        key="content",
        what="text extracted from the document (never the binary)",
        where="SQLite FTS5 table `documents_fts`",
        purpose="search and snippets",
        retention="with the document row; capped at 2 MB per document",
        deletion="`forget`, or `index` after deleting the file",
    ),
    DataItem(
        key="intelligence",
        what="language, headings, 24 terms, 16 co-occurrence pairs",
        where="SQLite table `document_intelligence`",
        purpose="related documents and discovery (phase 014)",
        retention="until rebuilt, cleared or the document is forgotten",
        deletion="`universal-search intelligence clear`",
        optional=True,
    ),
    DataItem(
        key="relationship_graph",
        what="bounded document nodes, term postings and explainable relationship edges",
        where=(
            "SQLite tables `document_graph_nodes`, `document_graph_terms`, "
            "`document_graph_edges` and `document_graph_metadata`"
        ),
        purpose="local related-document discovery (phase 022); never search ranking",
        retention="until rebuilt, cleared or the document is forgotten",
        deletion="`universal-search intelligence clear`, `privacy forget`, or rebuild",
        optional=True,
    ),
    DataItem(
        key="usage",
        what="document id + query text of opened results, timestamps",
        where="SQLite table `usage_events`",
        purpose="optional local ranking boost (phase 008)",
        retention="until cleared; disabled by default",
        deletion="`universal-search usage clear`, `diagnose repair all`",
        optional=True,
    ),
    DataItem(
        key="recents",
        what="recent query strings",
        where="application config (`config.json`)",
        purpose="the Recentes menu",
        retention="until cleared or the config is deleted",
        deletion="`universal-search recent clear`",
        optional=True,
    ),
    DataItem(
        key="process_coordination",
        what=(
            "process PIDs, startup generations, worker state/counters and request "
            "flags; never document text or query text"
        ),
        where=(
            "indexer lock/lease/owner, PID-scoped startup claims, generation-scoped "
            "stop markers, status files, gui PID/show/diagnostics flags and tray "
            "PID/lock files in the application home"
        ),
        purpose="single-instance ownership and local GUI/worker command handoff",
        retention=(
            "while a process owns a role; request flags are consumed and lease "
            "sidecars may remain as one-byte diagnostic files"
        ),
        deletion=(
            "stop the tray, GUI and indexer, then delete the listed coordination "
            "files or the application home"
        ),
    ),
    DataItem(
        key="control_state",
        what="source paths, scan counters and sanitized failure messages; never document text or query text",
        where="`control-center.json` in the application home",
        purpose="resume the operational control-center view after a restart",
        retention="until the next successful scan or the application home is deleted",
        deletion="delete `control-center.json` (the index and source files are unaffected)",
        optional=True,
    ),
    DataItem(
        key="logs",
        what="events, levels and paths — never document text",
        where="rotating `universal-search.log` in the application home",
        purpose="diagnosis; messages are capped at 500 characters",
        retention="1 MB x 3 rotated files",
        deletion="delete the log file",
    ),
    DataItem(
        key="metrics",
        what="latencies, counts, pass durations — no query text",
        where="`metrics.jsonl` in the application home",
        purpose="performance visibility (phase 011)",
        retention="compacted automatically past 512 KB",
        deletion="delete the file",
        optional=True,
    ),
)


@dataclass(frozen=True, slots=True)
class ForgetResult:
    """What ``forget`` removed (and what it deliberately left alone)."""

    path: str
    documents: int
    search_rows: int
    derived_rows: int
    usage_rows: int

    @property
    def total(self) -> int:
        return (
            self.documents + self.search_rows + self.derived_rows
            + self.usage_rows
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "documents": self.documents,
            "search_rows": self.search_rows,
            "derived_rows": self.derived_rows,
            "usage_rows": self.usage_rows,
            "total": self.total,
        }


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


PROCESS_COORDINATION_FILES = (
    "indexer.lock",
    "indexer.lock.lease",
    "indexer.lock.owner",
    "indexer.starting",
    "indexer.starting.lease",
    "indexer-status.json",
    "indexer-paused.flag",
    "indexer-stop.flag",
    "gui.pid",
    "gui-show.flag",
    "gui-diagnostics.flag",
    "tray.pid",
    "tray.pid.lock",
)

PROCESS_COORDINATION_PATTERNS = (
    "indexer.starting.*.claim",
    "indexer.starting.*.tmp",
    "indexer-stop.*.flag",
    "indexer-stop.*.flag.*.tmp",
)


def inventory_report(
    database: SearchDatabase, paths: AppPaths | None = None
) -> dict[str, object]:
    """The declared inventory plus the measured size of each location."""
    home = paths.home if paths is not None else Path(database.path).parent
    coordination_bytes = sum(
        _size(home / filename) for filename in PROCESS_COORDINATION_FILES
    )
    coordination_bytes += sum(
        _size(path)
        for pattern in PROCESS_COORDINATION_PATTERNS
        for path in home.glob(pattern)
    )
    measured: dict[str, int] = {
        "index": _size(Path(database.path)),
        "wal": _size(Path(str(database.path) + "-wal")),
        "shm": _size(Path(str(database.path) + "-shm")),
        "log": _size(home / "universal-search.log"),
        "metrics": _size(home / "metrics.jsonl"),
        "config": _size(home / "config.json"),
        "control_state": _size(home / "control-center.json"),
        "coordination": coordination_bytes,
    }
    return {
        "items": [item.as_dict() for item in INVENTORY],
        "bytes": measured,
        "application_home": str(home),
        "index": str(Path(database.path)),
        "leaves_machine": False,
    }


def forget(database: SearchDatabase, path: Path | str) -> ForgetResult:
    """Remove one document from the index **and** everything derived from it.

    The file itself is never touched: this is "stop indexing this", not
    "delete my file". Usage rows for the document go too — a privacy
    control that leaves a copy of the query that opened it behind would be
    theatre.
    """
    target = str(path)
    connection = database.connect()
    try:
        # Phase 024: two providers may own the same path, so a path can have
        # several rows. forget() must remove every one of them — leaving a
        # duplicate provider copy searchable would be a privacy leak.
        rows = connection.execute(
            "SELECT id, name, path FROM documents WHERE path = ?", (target,)
        ).fetchall()
        if not rows:
            # Bare-name fallback (what people paste from a listing): bounded
            # to a single row, as before.
            rows = connection.execute(
                "SELECT id, name, path FROM documents WHERE name = ?"
                " ORDER BY path LIMIT 1",
                (Path(target).name,),
            ).fetchall()
        if not rows:
            return ForgetResult(target, 0, 0, 0, 0)
        documents = 0
        search_rows = 0
        derived_rows = 0
        usage_rows = 0
        for row in rows:
            document_id = row["id"]
            search_rows += connection.execute(
                "DELETE FROM documents_fts WHERE document_id = ?", (document_id,)
            ).rowcount
            derived_rows += connection.execute(
                "DELETE FROM document_intelligence WHERE document_id = ?",
                (document_id,),
            ).rowcount
            # Phase 022 graph rows are derived from the same document and
            # must not survive a privacy-forget operation.  Keep them out of
            # the historical ``derived_rows`` count so the phase-018 API
            # stays stable.
            connection.execute(
                "DELETE FROM document_graph_edges WHERE source_document_id = ?"
                " OR target_document_id = ?",
                (document_id, document_id),
            )
            connection.execute(
                "DELETE FROM document_graph_terms WHERE document_id = ?",
                (document_id,),
            )
            connection.execute(
                "DELETE FROM document_graph_nodes WHERE document_id = ?",
                (document_id,),
            )
            from universal_search.intelligence.graph import scrub_reference_metadata

            scrub_reference_metadata(
                connection,
                [document_id],
                {document_id: (row["name"], row["path"])},
            )
            connection.execute(
                "DELETE FROM document_graph_metadata WHERE key = ?",
                (f"dirty:{document_id}",),
            )
            usage_rows += connection.execute(
                "DELETE FROM usage_events WHERE document_id = ?", (document_id,)
            ).rowcount
            documents += connection.execute(
                "DELETE FROM documents WHERE id = ?", (document_id,)
            ).rowcount
        connection.commit()
    finally:
        connection.close()
    return ForgetResult(
        target, documents, search_rows, derived_rows, usage_rows
    )
