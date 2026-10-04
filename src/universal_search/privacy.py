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
    # Phase 047. The lifecycle contract asks every persisted dataset for seven
    # things. Five were already here. These three had nowhere to go, which is
    # why the inventory could describe *what* is stored and *how to delete it*
    # but never *who rebuilds it* or *what happens on a schema upgrade* -- and
    # why a derived dataset with no rebuild path was indistinguishable from a
    # canonical one that cannot be rebuilt at all.
    owner: str = ""
    schema: str = ""
    rebuild: str = ""
    migration: str = ""

    @property
    def contract_complete(self) -> bool:
        """Whether all seven contract items are declared."""
        return all(
            (self.purpose, self.owner, self.schema, self.rebuild,
             self.retention, self.deletion, self.migration)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "what": self.what,
            "where": self.where,
            "purpose": self.purpose,
            "owner": self.owner,
            "schema": self.schema,
            "rebuild": self.rebuild,
            "retention": self.retention,
            "deletion": self.deletion,
            "migration": self.migration,
            "leaves_machine": self.leaves_machine,
            "optional": self.optional,
            "contract_complete": self.contract_complete,
        }


INVENTORY: tuple[DataItem, ...] = (
    DataItem(
        key="documents",
        what="path, name, size, timestamps, content hash",
        where="SQLite table `documents` in the index database",
        purpose="know what to index, detect changes, open the file",
        retention="until the file disappears or `forget`/rebuild removes it",
        deletion="`universal-search privacy forget <path>`, `diagnose repair all`",
        owner="`index.indexer`, the only writer of this table",
        schema=(
            "canonical; one row per (source, path); the schema version lives "
            "in `schema_migrations`"
        ),
        rebuild=(
            "any `index` pass rebuilds it from the filesystem; there is no "
            "other source for it"
        ),
        migration=(
            "`_migrate` adds columns in place; a file written by a newer build "
            "is refused with `UnsupportedSchemaVersion` rather than downgraded"
        ),
    ),
    DataItem(
        key="content",
        what="text extracted from the document (never the binary)",
        where="SQLite FTS5 table `documents_fts`",
        purpose="search and snippets",
        retention="with the document row; capped at 2 MB per document",
        deletion="`forget`, or `index` after deleting the file",
        owner="`extractors`, through the indexer's FTS upsert",
        schema=(
            "SQLite FTS5 with tokenizer `unicode61`; the tokenizer is the "
            "schema for search purposes"
        ),
        rebuild="re-derived from the document on every re-extraction",
        migration=(
            "a tokenizer change cannot be migrated, so the table is dropped "
            "and repopulated -- the corpus is the source of truth"
        ),
    ),
    DataItem(
        key="intelligence",
        what="language, headings, 24 terms, 16 co-occurrence pairs",
        where="SQLite table `document_intelligence`",
        purpose="related documents and discovery (phase 014)",
        retention="until rebuilt, cleared or the document is forgotten",
        deletion="`universal-search intelligence clear`",
        optional=True,
        owner="`intelligence.store`",
        schema="derived; the row's own `version` column is the contract version",
        rebuild="`universal-search intelligence rebuild`",
        migration=(
            "a version bump leaves rows stale and `rebuild` recomputes them; "
            "there is nothing to translate"
        ),
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
        owner="`intelligence.graph`",
        schema=(
            "derived; dirtiness lives in `document_graph_metadata` rather than "
            "a version column"
        ),
        rebuild="`universal-search intelligence rebuild relationships`",
        migration=(
            "a generation change forces a full rebuild instead of a migration, "
            "because edges are computed, not stored facts"
        ),
    ),
    DataItem(
        key="semantic_index",
        what="bounded local character n-gram vectors and content-word lists",
        where=(
            "SQLite tables `document_semantic`, `document_semantic_terms` "
            "and `document_semantic_metadata`"
        ),
        purpose="optional lexical-miss fallback retrieval (phase 026)",
        retention="until rebuilt, removed, the document is forgotten or the index is dropped",
        deletion="`privacy forget`, `semantic_index.remove_all()`, or full rebuild",
        optional=True,
        owner="`semantic.index`",
        schema=(
            "derived; vectors carry a model/version tag and the metadata row "
            "records it"
        ),
        rebuild="`SemanticIndex.rebuild()`, run lazily by `ensure_fresh`",
        migration=(
            "a tag change discards and rebuilds rather than translating "
            "vectors, which would be meaningless"
        ),
    ),
    DataItem(
        key="events",
        what="component, event id, severity, scope and error names; never text",
        where="JSON lines file `events.jsonl` (1 MB x 3 rotados)",
        purpose="operational traceability of control-center actions (phase 028)",
        retention="bounded by size; the oldest lines rotate out",
        deletion="borrar el fichero",
        optional=True,
        owner="`metrics`, written by the control center",
        schema="JSON lines, one object per line, no version field",
        rebuild="not rebuildable: it is an observation log",
        migration="the file rotates by size and is never translated",
    ),
    DataItem(
        key="fuzzy_index",
        what="at most 64 character trigrams per document, as blocking fingerprints",
        where=(
            "SQLite tables `document_fuzzy_documents` (integer surrogate to "
            "document hash), `document_fuzzy_terms` and `document_fuzzy_metadata`"
        ),
        purpose=(
            "propose candidates for typo and prefix queries (phase 031); the "
            "match is always decided against the document text"
        ),
        retention="until rebuilt, removed, the document is forgotten or the index is dropped",
        deletion="`privacy forget`, `FuzzyIndex.remove_all()`, or full rebuild",
        optional=True,
        owner="`fuzzy.index`",
        schema=(
            "derived; a surrogate table maps to the document hash and the "
            "metadata row records the per-document trigram cap"
        ),
        rebuild="`FuzzyIndex.rebuild()`, run lazily by `ensure_fresh`",
        migration=(
            "changing the cap invalidates fingerprints and rebuilds them; the "
            "document text is the source of truth"
        ),
    ),
    DataItem(
        key="usage",
        what="document id + query text of opened results, timestamps",
        where="SQLite table `usage_events`",
        purpose=(
            "optional local ranking boost, scoped to the query it was recorded "
            "under and decayed by age (phase 044)"
        ),
        retention=(
            "rows are kept until cleared, but their influence fades on a fixed "
            "schedule -- full weight for 30 days, then 0.6/0.3/0.1 -- so an old "
            "habit stops steering results even though the row is still there; "
            "disabled by default"
        ),
        deletion=(
            "`universal-search usage clear`, `privacy forget`, deletion of the "
            "document, `diagnose repair all`"
        ),
        optional=True,
        owner="`learn`, written when a result is opened",
        schema=(
            "derived from behaviour; rows decay by age rather than carrying a "
            "schema version"
        ),
        rebuild="not rebuildable: it records what happened, not a fact",
        migration=(
            "a schema change drops the rows -- they are re-learned over time or "
            "not at all, which is the honest outcome"
        ),
    ),
    DataItem(
        key="recents",
        what="recent query strings",
        where="application config (`config.json`)",
        purpose="the Recentes menu",
        retention="until cleared or the config is deleted",
        deletion="`universal-search recent clear`",
        optional=True,
        owner="the GUI, through the application config",
        schema="`config.json`; no version field",
        rebuild="not rebuildable: it is user input",
        migration="unknown keys are preserved and ignored, never discarded",
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
        owner="the launcher, worker and tray, each for its own role",
        schema="generation numbers in the file names",
        rebuild="not rebuildable; recreated by the next launch",
        migration=(
            "a stale generation is recognised and discarded on sight, which is "
            "the whole purpose of putting it in the name"
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
        owner="`control_center`, written after each scan",
        schema="counters and source paths keyed by root",
        rebuild="recreated by the next scan",
        migration=(
            "a source that no longer exists is dropped on the next scan rather "
            "than migrated"
        ),
    ),
    DataItem(
        key="logs",
        what="events, levels and paths — never document text",
        where="rotating `universal-search.log` in the application home",
        purpose="diagnosis; messages are capped at 500 characters",
        retention="1 MB x 3 rotated files",
        deletion="delete the log file",
        owner="the CLI and GUI bootstrap",
        schema="rotating text, messages capped at 500 characters",
        rebuild="not rebuildable: it is a diagnostic trail",
        migration="rotated out by size, never translated",
    ),
    DataItem(
        key="fts_shadow",
        what=(
            "the FTS5 shadow tables: inverted index segments, per-document "
            "sizes, the stored column values and the tokenizer config"
        ),
        where=(
            "SQLite tables `documents_fts_data`, `documents_fts_idx`, "
            "`documents_fts_docsize`, `documents_fts_content` and "
            "`documents_fts_config`"
        ),
        purpose=(
            "the actual search index. Phase 047 measured that these are "
            "invisible to anything reading `sqlite_master` by declared name, "
            "while holding the largest share of the database"
        ),
        retention="with the `content` category; dropped and repopulated with it",
        deletion=(
            "`privacy forget` and `DROP`ping `documents_fts`; never deleted "
            "directly, because SQLite owns them"
        ),
        owner="SQLite's FTS5, driven by the indexer's insert into `documents_fts`",
        schema="SQLite-internal; the shape is FTS5's, not the project's",
        rebuild=(
            "rebuilt by re-inserting into `documents_fts`, or by FTS5's own "
            "'rebuild' command when the content table survives"
        ),
        migration=(
            "owned by the SQLite build; a tokenizer or FTS version change means "
            "dropping the virtual table, never altering these"
        ),
        optional=True,
    ),
    DataItem(
        key="schema_migrations",
        what="the ordered record of every schema version applied to this file",
        where="SQLite table `schema_migrations`",
        purpose=(
            "tells a user what has been done to their index, and stops a "
            "database written by a newer build being opened and half-understood"
        ),
        retention="as long as the index exists; one row per applied version",
        deletion="never; deleting it would make the version unknowable",
        owner="`SearchDatabase._migrate`, the only writer",
        schema="`(version INTEGER, applied_at TEXT)`, appended to, never rewritten",
        rebuild=(
            "not rebuildable from the file: it is the one table that records "
            "history, so its absence is itself information"
        ),
        migration=(
            "this table is what a migration writes; a future version is "
            "refused with `UnsupportedSchemaVersion` rather than guessed at"
        ),
        optional=True,
    ),
    DataItem(
        key="metrics",
        what="latencies, counts, pass durations — no query text",
        where="`metrics.jsonl` in the application home",
        purpose="performance visibility (phase 011)",
        retention="compacted automatically past 512 KB",
        deletion="delete the file",
        optional=True,
        owner="`metrics`",
        schema="JSON lines with a fixed key set",
        rebuild="not rebuildable: it is a measurement, not a fact",
        migration="unknown keys are ignored; the file compacts past 512 KB",
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


#: Which declared category each table belongs to. Explicit on purpose: a
#: category that cannot be enumerated is not a category, and this mapping is
#: what lets the storage gate assert that *every* table has a declaration.
CATEGORY_TABLES: dict[str, tuple[str, ...]] = {
    "documents": ("documents",),
    "content": ("documents_fts",),
    "fts_shadow": (
        "documents_fts_data", "documents_fts_idx", "documents_fts_docsize",
        "documents_fts_content", "documents_fts_config",
    ),
    "intelligence": ("document_intelligence",),
    "relationship_graph": (
        "document_graph_nodes", "document_graph_terms",
        "document_graph_edges", "document_graph_metadata",
    ),
    "semantic_index": (
        "document_semantic", "document_semantic_terms",
        "document_semantic_metadata",
    ),
    "fuzzy_index": (
        "document_fuzzy_documents", "document_fuzzy_terms",
        "document_fuzzy_metadata",
    ),
    "usage": ("usage_events",),
    "schema_migrations": ("schema_migrations",),
}

#: Tables SQLite owns. Declared as an exclusion with a name so the gap is a
#: decision somebody can disagree with, not an oversight.
SQLITE_INTERNAL_PREFIX = "sqlite_"

#: Datasets whose bytes are the index itself rather than a derivation of it.
CANONICAL_CATEGORIES = ("documents", "content", "fts_shadow", "schema_migrations")

#: Why this build cannot report bytes per table. Measured, not assumed:
#: `dbstat`, `sqlite_dbpage`, `sqlite_stat1` and `sqlite_stat4` all raise
#: `no such table`, and `PRAGMA compile_options` lists only ENABLE_FTS3/4/5 and
#: ENABLE_RTREE. Row counts below are exact; a byte split per table would be a
#: guess, so this project does not print one.
NO_PER_TABLE_BYTES = (
    "per-table bytes are unavailable in this build: dbstat, sqlite_dbpage and "
    "sqlite_stat1/4 are all absent (measured; compile_options shows only "
    "ENABLE_FTS3/4/5 and ENABLE_RTREE), so no row count here is converted into "
    "an estimated byte figure"
)


def _row_counts(connection) -> dict[str, int]:
    """Exact row count per table, grouped by declared category."""
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    counts: dict[str, int] = {}
    for category, tables in CATEGORY_TABLES.items():
        total = 0
        for table in tables:
            if table in present:
                total += connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
        counts[category] = total
    return counts


def undeclared_tables(connection) -> list[str]:
    """Tables in this database that no inventory item claims.

    The storage gate asserts this is empty, which is what turns "the lifecycle
    contract is declared for every dataset" into something a machine checks
    rather than something a document claims.
    """
    declared = {table for tables in CATEGORY_TABLES.values() for table in tables}
    return sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        if row[0] not in declared
        and not row[0].startswith(SQLITE_INTERNAL_PREFIX)
    )


def storage_report(
    database: SearchDatabase, paths: AppPaths | None = None
) -> dict[str, object]:
    """What this build can measure exactly, and an honest note about the rest.

    Phase 047. Before this, `inventory_report` answered "how big is the index
    file" and nothing else, which is true and useless for the question a user
    actually has -- *why is my index 80 MB when my documents are 4 MB?*

    The measured answer, on 1000 documents with the three derived layers built:

        canonical index (documents + FTS5)   3,801,088 bytes   4.8%
        semantic derived                    55,799,808 bytes  70.6%
        fuzzy derived                        4,395,008 bytes   5.6%
        graph derived                       15,130,624 bytes  19.2%

    The byte split above is measurable only offline, by building the corpus with
    and without each layer, and it is recorded in the phase report and the gate
    baseline rather than printed here. What this function returns at runtime is
    exact: file bytes, page accounting, and row counts per category.
    """
    inventory = inventory_report(database, paths)
    home = Path(inventory["application_home"])
    connection = database.connect()
    try:
        counts = _row_counts(connection)
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        freelist = connection.execute("PRAGMA freelist_count").fetchone()[0]
        undeclared = undeclared_tables(connection)
    finally:
        connection.close()

    files = dict(inventory["bytes"])
    index_total = files["index"] + files["wal"] + files["shm"]
    canonical_rows = sum(counts.get(key, 0) for key in CANONICAL_CATEGORIES)
    derived_rows = sum(
        count for key, count in counts.items()
        if key not in CANONICAL_CATEGORIES
    )
    return {
        "index": str(Path(database.path)),
        "application_home": str(home),
        "bytes": files,
        "index_bytes": index_total,
        "pages": page_count,
        "page_size": page_size,
        "freelist_pages": freelist,
        # A floor, not a quote. Free pages are what SQLite has already
        # released; a VACUUM also defragments partially-used pages, so it
        # normally returns more than this. Measured on 400 documents: the
        # report offered 20.0 KiB and compact() handed back 84.0 KiB, because
        # 64 KiB of that came from pages SQLite never freed. Printing the
        # number as a promise would be wrong in the other direction, so it is
        # labelled as what it is.
        "reclaimable_bytes": freelist * page_size,
        "reclaimable_is_floor": True,
        "rows": counts,
        "canonical_rows": canonical_rows,
        "derived_rows": derived_rows,
        "row_counts_exact": True,
        "per_table_bytes": None,
        "per_table_bytes_note": NO_PER_TABLE_BYTES,
        "undeclared_tables": undeclared,
        "reclaim_command": (
            "`universal-search storage compact` -- without it, purging "
            "derived data frees pages but not bytes; compact() returns at "
            "least the reclaimable amount reported here, and usually more"
        ),
        "contract_incomplete": [
            item.key for item in INVENTORY if not item.contract_complete
        ],
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
            # Phase 026 semantic vectors are derived from the same document
            # text. They are personal data too: a forgotten document must
            # not remain retrievable through the optional fallback.
            derived_rows += connection.execute(
                "DELETE FROM document_semantic_terms WHERE document_id = ?",
                (document_id,),
            ).rowcount
            derived_rows += connection.execute(
                "DELETE FROM document_semantic WHERE document_id = ?",
                (document_id,),
            ).rowcount
            # Phase 031 blocking fingerprints are derived from the same
            # document text. They are personal data too: a forgotten document
            # must not stay reachable through the fuzzy fallback.
            derived_rows += connection.execute(
                "DELETE FROM document_fuzzy_terms WHERE surrogate IN"
                " (SELECT surrogate FROM document_fuzzy_documents"
                "  WHERE document_id = ?)",
                (document_id,),
            ).rowcount
            derived_rows += connection.execute(
                "DELETE FROM document_fuzzy_documents WHERE document_id = ?",
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
