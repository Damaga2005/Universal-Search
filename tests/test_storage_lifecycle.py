"""Phase 047: the storage footprint has to be visible and controllable.

Two defects drove this phase, and both are invisible until you run the thing a
user runs.

The first is `maintenance(vacuum=True)`, which reclaimed **0 of 74,956,800
bytes** while reporting 18,038 pages freed. The cause was ordering: in WAL mode
a VACUUM writes the rewrite into the WAL and the main file is only truncated at
a later checkpoint, and the method checkpointed before vacuuming and never
after.

The second is that nothing told a user the index was 95% derived. After
indexing, the semantic, fuzzy and graph tables hold *zero* rows -- they are
built the first time a feature uses them -- so the 71 MiB appears between two
searches with no command run and nothing logged. Measured: 293,252 derived rows
against 4,293 canonical ones, for 1,000 documents.

Everything below pins one of those, or the honesty rules that keep the report
from inventing numbers it cannot measure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_search.index.database import (
    DatabaseCompactionError,
    SearchDatabase,
)
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.privacy import (
    CATEGORY_TABLES,
    INVENTORY,
    storage_report,
    undeclared_tables,
)


def _corpus(root: Path, count: int = 40) -> Path:
    for index in range(count):
        target = root / "docs" / f"doc{index}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"documento {index} sobre amplificadores y transistores "
            f"con contenido real para buscar",
            encoding="utf-8",
        )
    return root


def _rows(database: SearchDatabase, sql: str, params: tuple = ()) -> list:
    connection = database.connect()
    try:
        cursor = connection.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


# -- the defect: compacting has to return the bytes --------------------------

def test_compacting_returns_bytes_instead_of_claiming_to(tmp_path):
    """The whole point of the operation.

    The old behaviour, on this same shape of data: 78,745,600 bytes before,
    78,745,600 after, and a report saying 18,036 pages had been reclaimed.
    """
    tree = _corpus(tmp_path / "tree")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    path = Path(database.path)

    # Create real slack: insert a lot, then delete it.
    connection = database.connect()
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scratch (id INTEGER PRIMARY KEY, v TEXT)"
        )
        connection.executemany(
            "INSERT INTO scratch (v) VALUES (?)",
            [("palabra " * 40,) for _ in range(4000)],
        )
        connection.commit()
    finally:
        connection.close()
    connection = database.connect()
    try:
        connection.execute("DELETE FROM scratch")
        connection.execute("DROP TABLE scratch")
        connection.commit()
    finally:
        connection.close()

    before = path.stat().st_size
    free_before = _rows(database, "PRAGMA freelist_count")[0][0]
    assert free_before > 100, (
        f"the scenario needs real slack to be meaningful: {free_before} pages"
    )

    result = database.compact()

    after = path.stat().st_size
    assert result["bytes_reclaimed"] == before - after, (
        "compact() reported a figure that does not match the file on disk"
    )
    assert after < before * 0.5, (
        f"compacting returned almost nothing: {before:,} -> {after:,}"
    )
    assert result["bytes_reclaimed"] >= free_before * 4096, (
        "compact() returned less than the free pages it was promised"
    )


def test_compacting_never_costs_a_document_or_a_search_hit(tmp_path):
    """Verification happens before the swap, not after."""
    tree = _corpus(tmp_path / "tree", 60)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    before_docs = len(_rows(database, "SELECT id FROM documents"))
    before_fts = len(_rows(database, "SELECT document_id FROM documents_fts"))
    term = "amplificadores"
    before_hits = len(_rows(
        database,
        "SELECT document_id FROM documents_fts WHERE documents_fts MATCH ?",
        (term,),
    ))
    assert before_hits == 60, before_hits

    database.compact()

    assert len(_rows(database, "SELECT id FROM documents")) == before_docs
    assert len(_rows(database, "SELECT document_id FROM documents_fts")) == before_fts
    assert len(_rows(
        database,
        "SELECT document_id FROM documents_fts WHERE documents_fts MATCH ?",
        (term,),
    )) == before_hits
    connection = database.connect()
    try:
        assert SearchEngine(database).search(term, limit=5)
    finally:
        connection.close()


def test_compacting_twice_is_stable_and_leaves_no_artefacts(tmp_path):
    """Idempotence, and no `.compact` file left on disk."""
    tree = _corpus(tmp_path / "tree", 30)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    first = database.compact()
    second = database.compact()

    assert second["bytes_reclaimed"] == 0, (
        f"a second compaction changed the file by "
        f"{second['bytes_reclaimed']:,} bytes; there was nothing left to give"
    )
    assert second["bytes_after"] == first["bytes_after"]
    leftovers = sorted(
        p.name for p in tmp_path.iterdir()
        if ".compact" in p.name or p.name.endswith(".bak")
    )
    assert not leftovers, f"compaction left artefacts behind: {leftovers}"


def test_a_refused_compaction_leaves_the_original_untouched(tmp_path, monkeypatch):
    """The failure mode has to be safe, not merely reported.

    A candidate that does not verify is discarded and the original database is
    still there -- that is the whole reason the swap is two-phase.

    The fault is injected into the *verification*, not into SQLite. The first
    attempt patched `sqlite3.connect` to add a table to the candidate; adding a
    table does not fail `integrity_check`, so nothing raised and the test proved
    nothing. Corrupting a real database in a unit test costs more than it buys,
    so this makes the row-count comparison -- the guard that actually protects
    the original -- report a loss, and checks that the loss is refused.
    """
    tree = _corpus(tmp_path / "tree", 10)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    path = Path(database.path)
    before = path.stat().st_size
    documents = len(_rows(database, "SELECT id FROM documents"))

    real_counts = SearchDatabase._row_counts
    calls = {"n": 0}

    def lying_counts(connection):
        """The first call is the *expectation*, the second reads the candidate.

        Claiming one more document than the candidate will report is exactly
        the "compact candidate lost rows" case.
        """
        calls["n"] += 1
        found = real_counts(connection)
        if calls["n"] == 1:
            found = dict(found)
            found["documents"] = found.get("documents", 0) + 1
        return found

    monkeypatch.setattr(SearchDatabase, "_row_counts", staticmethod(lying_counts))

    with pytest.raises(DatabaseCompactionError) as caught:
        database.compact()

    assert "documents" in str(caught.value), (
        f"the refusal should say what it found wrong: {caught.value}"
    )
    assert path.exists(), "the original database was removed"
    assert path.stat().st_size == before
    assert len(_rows(database, "SELECT id FROM documents")) == documents
    assert not list(tmp_path.glob("*.compact")), (
        "a refused candidate must not be left on disk"
    )


def test_maintenance_with_vacuum_is_compaction_not_a_vacuum_that_lies(tmp_path):
    """The old entry point still works, and now means what it says."""
    tree = _corpus(tmp_path / "tree", 10)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    result = database.maintenance(vacuum=True)

    assert "bytes_reclaimed" in result, (
        "maintenance(vacuum=True) went back to reporting freelist counts, "
        "which is the defect this phase removed"
    )
    assert result["bytes_reclaimed"] >= 0


# -- the accounting has to be exact, and admit what it cannot do --------------

def test_no_table_exists_without_a_declared_dataset(tmp_path):
    """The contract is only complete if it covers the schema that exists."""
    tree = _corpus(tmp_path / "tree", 20)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    connection = database.connect()
    try:
        missing = undeclared_tables(connection)
        present = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
    finally:
        connection.close()

    assert not missing, (
        f"tables nobody declares a lifecycle for: {missing} "
        f"(schema has {len(present)} tables)"
    )


def test_the_report_refuses_to_estimate_bytes_per_table(tmp_path):
    """It cannot measure them, so it must not print them.

    `dbstat`, `sqlite_dbpage` and `sqlite_stat1/4` are all absent from this
    build. A per-table byte figure would be an invention wearing a decimal
    point, and this is the assertion that keeps it from ever being one.
    """
    tree = _corpus(tmp_path / "tree", 20)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    report = storage_report(database)

    assert report["per_table_bytes"] is None
    assert "dbstat" in report["per_table_bytes_note"]
    assert report["row_counts_exact"] is True


def test_the_reclaimable_figure_is_a_floor_and_says_so(tmp_path):
    """It counts pages SQLite has already released; a VACUUM returns more."""
    tree = _corpus(tmp_path / "tree", 20)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    report = storage_report(database)

    assert report["reclaimable_is_floor"] is True
    assert report["reclaimable_bytes"] == (
        report["freelist_pages"] * report["page_size"]
    )
    assert "at least" in report["reclaim_command"]


def test_the_report_separates_canonical_from_derived(tmp_path):
    """The number that answers "why is my index so big".

    Measured at 1000 documents: 4,293 canonical rows against 293,252 derived
    ones, and 71 MiB against 4 MiB.
    """
    tree = _corpus(tmp_path / "tree", 40)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    report = storage_report(database)

    assert report["canonical_rows"] > 0
    assert report["derived_rows"] > 0
    assert report["canonical_rows"] == sum(
        report["rows"][key] for key in
        ("documents", "content", "fts_shadow", "schema_migrations")
    )
    for key in ("semantic_index", "fuzzy_index", "relationship_graph"):
        assert key in report["rows"], (
            f"{key} is missing from the report; a derived layer nobody can "
            f"see is a layer nobody can turn off"
        )


def test_derived_layers_are_absent_until_a_feature_uses_them(tmp_path):
    """Why the footprint is invisible until it is alarming.

    This is the fact that makes the storage story hard, and it is worth
    pinning: nothing derived is built at index time, so the index is small
    after indexing and enormous after the first fallback search.
    """
    tree = _corpus(tmp_path / "tree", 30)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    # Category counts include a metadata row that exists from the start, so
    # laziness has to be asserted on the vectors themselves. The first version
    # asserted `semantic_index == 0` and failed with 1 -- which is the metadata
    # row, not a vector, and says nothing about whether anything was built.
    def vectors() -> dict[str, int]:
        return {
            "semantic": len(_rows(database, "SELECT document_id FROM document_semantic")),
            "terms": len(_rows(database, "SELECT document_id FROM document_semantic_terms")),
            "fuzzy": len(_rows(database, "SELECT surrogate FROM document_fuzzy_documents")),
            "graph": len(_rows(database, "SELECT document_id FROM document_graph_nodes")),
        }

    before = storage_report(database)
    assert vectors() == {"semantic": 0, "terms": 0, "fuzzy": 0, "graph": 0}

    from universal_search.semantic.index import SemanticIndex

    SemanticIndex(database).ensure_fresh()
    after = storage_report(database)

    assert vectors()["semantic"] == 30
    # The 200 content words per document is a *cap*, not a target, and these
    # fixtures are a sentence long. Asserting the cap exactly failed with 2,360
    # terms against an expected 6,000, which says the documents are short, not
    # that the store is unbounded.
    assert 0 < vectors()["terms"] <= 30 * 200, vectors()["terms"]
    assert after["derived_rows"] > before["derived_rows"]
    # The file does grow here, and that growth is the point: the derived data
    # appears between two operations with nothing announcing it. The previous
    # version of this test asserted the opposite -- that the bytes stayed equal
    # -- as a "this test only asserts laziness" sanity check, which contradicted
    # the very thing it was documenting.
    assert after["index_bytes"] > before["index_bytes"], (
        "building a derived layer should cost disk; if it no longer does, the "
        "phase report's byte figures need re-measuring"
    )


# -- the declared contract itself ---------------------------------------------

def test_every_dataset_declares_all_seven_contract_items():
    """Purpose, owner, schema, rebuild, retention, deletion, migration.

    Three of these had nowhere to live before phase 047, which is why a derived
    dataset with no rebuild path was indistinguishable from a canonical one
    that cannot be rebuilt at all.
    """
    incomplete = [item.key for item in INVENTORY if not item.contract_complete]
    assert not incomplete, f"datasets with an incomplete contract: {incomplete}"
    for item in INVENTORY:
        assert item.owner, item.key
        assert item.schema, item.key
        assert item.rebuild, item.key
        assert item.migration, item.key


def test_the_fts_shadow_tables_are_declared_somewhere():
    """They hold the actual inverted index and had no declaration at all."""
    declared = {table for tables in CATEGORY_TABLES.values() for table in tables}
    for shadow in (
        "documents_fts_data", "documents_fts_idx", "documents_fts_docsize",
        "documents_fts_content", "documents_fts_config",
    ):
        assert shadow in declared, (
            f"{shadow} is a real table holding the search index and no "
            f"inventory item claims it"
        )


def test_compaction_never_touches_the_users_documents(tmp_path):
    """Stated in the command's help text, so it had better be true."""
    tree = _corpus(tmp_path / "tree", 15)
    before = {
        path.name: (path.stat().st_size, path.read_bytes())
        for path in sorted(tree.rglob("*")) if path.is_file()
    }
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    database.compact()

    after = {
        path.name: (path.stat().st_size, path.read_bytes())
        for path in sorted(tree.rglob("*")) if path.is_file()
    }
    assert before == after