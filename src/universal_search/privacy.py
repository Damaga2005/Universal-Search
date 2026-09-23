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


def inventory_report(
    database: SearchDatabase, paths: AppPaths | None = None
) -> dict[str, object]:
    """The declared inventory plus the measured size of each location."""
    home = paths.home if paths is not None else Path(database.path).parent
    measured: dict[str, int] = {
        "index": _size(Path(database.path)),
        "wal": _size(Path(str(database.path) + "-wal")),
        "shm": _size(Path(str(database.path) + "-shm")),
        "log": _size(home / "universal-search.log"),
        "metrics": _size(home / "metrics.jsonl"),
        "config": _size(home / "config.json"),
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
        row = connection.execute(
            "SELECT id FROM documents WHERE path = ?", (target,)
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT id FROM documents WHERE name = ?", (Path(target).name,)
            ).fetchone()
        if row is None:
            return ForgetResult(target, 0, 0, 0, 0)
        document_id = row["id"]
        search_rows = connection.execute(
            "DELETE FROM documents_fts WHERE document_id = ?", (document_id,)
        ).rowcount
        derived_rows = connection.execute(
            "DELETE FROM document_intelligence WHERE document_id = ?",
            (document_id,),
        ).rowcount
        usage_rows = connection.execute(
            "DELETE FROM usage_events WHERE document_id = ?", (document_id,)
        ).rowcount
        documents = connection.execute(
            "DELETE FROM documents WHERE id = ?", (document_id,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return ForgetResult(
        target, documents, search_rows, derived_rows, usage_rows
    )
