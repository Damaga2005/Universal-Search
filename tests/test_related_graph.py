"""Phase 022: bounded, deterministic related-document graph tests."""

import json
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.intelligence.analysis import DocumentRecord, analyze
from universal_search.intelligence.graph import (
    GRAPH_SCHEMA_VERSION,
    MAX_AGGREGATE_CANDIDATE_IDS,
    MAX_CANDIDATES_PER_DOCUMENT,
    MAX_EDGES_PER_DOCUMENT,
    MAX_GRAPH_TERMS,
    MAX_POSTINGS_PER_TERM,
    MIN_GRAPH_SIMILARITY,
    GraphStore,
)


def record(
    document_id: str,
    text: str,
    name: str | None = None,
    path: str | None = None,
    *,
    source: str = "local",
    context: str = "",
) -> DocumentRecord:
    filename = name or f"{document_id}.md"
    filepath = path or f"docs/{filename}"
    return DocumentRecord(
        document_id=document_id,
        name=filename,
        path=filepath,
        source=source,
        content_hash=f"hash-{document_id}",
        analysis=analyze(text, name=filename),
        content=text,
        context=context,
    )


def make_store(tmp_path: Path, records: list[DocumentRecord]) -> GraphStore:
    database = SearchDatabase(tmp_path / "index.db")
    return GraphStore(database), records


def edge_rows(store: GraphStore) -> list[tuple]:
    with store.database.connect() as connection:
        return [
            (
                row["source_document_id"],
                row["target_document_id"],
                row["edge_type"],
                round(float(row["weight"]), 8),
                row["evidence"],
                row["version"],
                row["generation"],
            )
            for row in connection.execute(
                "SELECT * FROM document_graph_edges"
                " ORDER BY source_document_id, target_document_id, edge_type"
            )
        ]


def test_rebuild_writes_nodes_and_edges_in_deterministic_order(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("z", "CMOS logic gates and transistor circuits", "z.md", "docs/z.md"),
            record(
                "a",
                "BJT transistor biasing with the Ebers-Moll model",
                "a.md",
                "docs/a.md",
            ),
            record("m", "MUX selection and CMOS logic", "m.md", "docs/m.md"),
        ],
    )

    first = store.rebuild(records)
    first_edges = edge_rows(store)
    second = store.rebuild(list(reversed(records)))

    assert first.nodes_written == 3
    assert first.edges_written == len(first_edges)
    assert second.nodes_written == 3
    assert second.edges_written == len(first_edges)
    assert edge_rows(store) == first_edges
    assert all(row[5] == GRAPH_SCHEMA_VERSION for row in first_edges)
    assert all(row[5] == GRAPH_SCHEMA_VERSION for row in edge_rows(store))


def test_bjt_ebers_moll_relationship_is_explainable(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record(
                "source",
                "BJT transistor operating point and Ebers-Moll equations",
                "source.md",
                "electronics/source.md",
            ),
            record(
                "notes",
                "Notes about the BJT transistor and Ebers-Moll model bias",
                "notes.md",
                "electronics/notes.md",
            ),
            record("paella", "Paella rice and saffron", "paella.md", "recipes/paella.md"),
        ],
    )
    store.rebuild(records)

    related = store.related("source")

    assert [item.document_id for item in related] == ["notes"]
    assert related[0].score >= MIN_GRAPH_SIMILARITY
    assert related[0].score <= 1.0
    evidence = related[0].evidence
    assert evidence
    assert any(item.kind in {"shared_terms", "keyword_overlap", "phrase_overlap"} for item in evidence)
    assert any("bjt" in item.values for item in evidence)
    assert any("ebers" in " ".join(item.values) for item in evidence)
    assert all(0.0 < item.weight <= 1.0 for item in evidence)


def test_safe_explicit_reference_creates_a_candidate_across_directories(
    tmp_path: Path,
):
    store, records = make_store(
        tmp_path,
        [
            record(
                "source",
                "The companion file notes.md contains the circuit details.",
                "source.md",
                "one/source.md",
            ),
            record("target", "Different subject", "notes.md", "two/notes.md"),
        ],
    )
    store.rebuild(records)

    result = store.related("source")
    assert [item.document_id for item in result] == ["target"]
    assert any(item.kind == "explicit_reference" for item in result[0].evidence)


def test_explicit_reference_direction_is_symmetric_and_incremental(
    tmp_path: Path,
):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "low.md").write_text("alpha unique subject", encoding="utf-8")
    (tree / "high.md").write_text(
        "See low.md for beta details", encoding="utf-8"
    )
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tree)
    store = GraphStore(database)
    store.rebuild_from_database()
    with database.connect() as connection:
        low_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'low.md'"
        ).fetchone()["id"]
        high_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'high.md'"
        ).fetchone()["id"]

    assert [item.document_id for item in store.related(low_id)] == [high_id]
    assert [item.document_id for item in store.related(high_id)] == [low_id]

    (tree / "high.md").write_text(
        "Updated low.md reference with gamma details", encoding="utf-8"
    )
    indexer.index_root(tree)

    refreshed = store.related(low_id)
    assert [item.document_id for item in refreshed] == [high_id]
    assert any(item.kind == "explicit_reference" for item in refreshed[0].evidence)

    (tree / "low.md").write_text("alpha changed subject", encoding="utf-8")
    indexer.index_root(tree)
    assert [item.document_id for item in store.related(low_id)] == [high_id]


def test_unrelated_documents_are_not_stored_as_edges(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("bjt", "transistor biasing active region", "bjt.md", "docs/bjt.md"),
            record("recipe", "paella saffron rice", "recipe.md", "docs/recipe.md"),
        ],
    )
    store.rebuild(records)

    assert store.related("bjt") == []
    assert store.related("recipe") == []
    assert edge_rows(store) == []


def test_rebuild_is_stable_for_identical_documents(tmp_path: Path):
    text = "BJT Ebers-Moll transistor model"
    store, records = make_store(
        tmp_path,
        [
            record("left", text, "left.md", "docs/left.md"),
            record("right", text, "right.md", "docs/right.md"),
        ],
    )

    first = store.rebuild(records)
    first_edges = edge_rows(store)
    second = store.rebuild(records)

    assert first.edges_written == second.edges_written == 1
    assert edge_rows(store) == first_edges
    assert store.related("left")[0].document_id == "right"


def test_rebuild_removes_edges_for_removed_records(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "CMOS transistor circuit", "a.md"),
            record("b", "CMOS transistor circuit notes", "b.md"),
            record("c", "CMOS transistor circuit extra", "c.md"),
        ],
    )
    store.rebuild(records)
    assert store.related("a")

    store.rebuild([records[0], records[2]])

    with store.database.connect() as connection:
        ids = {
            row["document_id"]
            for row in connection.execute("SELECT document_id FROM document_graph_nodes")
        }
    assert "b" not in ids
    assert "b" not in {item.document_id for item in store.related("a")}
    assert "c" in {item.document_id for item in store.related("a")}


def test_invalidate_removes_a_missing_node_and_its_edges(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "CMOS transistor circuit", "a.md"),
            record("b", "CMOS transistor circuit notes", "b.md"),
        ],
    )
    store.rebuild(records)

    stats = store.invalidate(["b"])

    assert stats.nodes_removed == 1
    assert stats.edges_removed == 1
    assert store.related("a") == []
    with store.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_edges"
            " WHERE source_document_id = 'b' OR target_document_id = 'b'"
        ).fetchone()[0] == 0


def test_invalidate_recomputes_only_affected_relationships(tmp_path: Path):
    database = SearchDatabase(tmp_path / "index.db")
    store = GraphStore(database)
    original = record("a", "BJT transistor and Ebers-Moll model", "a.md", "docs/a.md")
    neighbor = record("b", "BJT transistor bias notes", "b.md", "docs/b.md")
    unrelated = record("c", "CMOS logic gates", "c.md", "docs/c.md")
    store.rebuild([original, neighbor, unrelated])

    # A canonical indexed document is updated; the graph must refresh the
    # affected pair without losing an unrelated node.
    from universal_search.domain.document import Document, SourceKind
    from datetime import datetime, timezone
    import hashlib

    def upsert(item: DocumentRecord, text: str) -> None:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        Indexer(database).upsert(
            Document(
                id=item.document_id,
                source=SourceKind(item.source),
                path=Path(item.path),
                name=item.name,
                extension=Path(item.path).suffix,
                size=len(text),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                content=text,
                content_hash=content_hash,
            )
        )

    for item in (original, neighbor, unrelated):
        upsert(item, item.content or "")
    changed = record("a", "CMOS MUX selection logic", "a.md", "docs/a.md")
    upsert(changed, changed.content or "")
    stats = store.invalidate(["a"])

    assert stats.nodes_written == 1
    assert "a" in {row["document_id"] for row in _nodes(store)}
    assert "b" not in {item.document_id for item in store.related("a")}
    assert "c" in {item.document_id for item in store.related("a")}


def test_incremental_invalidation_does_not_load_the_whole_corpus(
    tmp_path: Path, monkeypatch
):
    import universal_search.intelligence.graph as graph_module

    tree = tmp_path / "tree"
    tree.mkdir()
    for index in range(40):
        (tree / f"doc-{index:02d}.md").write_text(
            f"topic{index} sharedterm subject{index}", encoding="utf-8"
        )
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    store = GraphStore(database)
    store.rebuild_from_database()
    with database.connect() as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'doc-00.md'"
        ).fetchone()["id"]
    calls: list[object] = []
    original = graph_module._records_from_database

    def spy(db, document_ids=None):
        calls.append(document_ids)
        return original(db, document_ids)

    monkeypatch.setattr(graph_module, "_records_from_database", spy)
    stats = store.invalidate([document_id])

    assert calls
    assert all(value is not None for value in calls)
    assert stats.records_loaded <= MAX_CANDIDATES_PER_DOCUMENT + 2


def _nodes(store: GraphStore) -> list:
    with store.database.connect() as connection:
        return connection.execute("SELECT * FROM document_graph_nodes").fetchall()


def test_indexer_deletion_removes_graph_rows(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("BJT transistor Ebers-Moll", encoding="utf-8")
    (tree / "b.md").write_text("BJT transistor Ebers-Moll notes", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tree)
    GraphStore(database).rebuild_from_database()
    (tree / "b.md").unlink()

    indexer.index_root(tree)

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_nodes WHERE document_id ="
            " (SELECT id FROM documents WHERE name = 'b.md')"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_edges WHERE target_document_id ="
            " (SELECT id FROM documents WHERE name = 'b.md')"
        ).fetchone()[0] == 0


def test_indexer_deletion_scrubs_direct_and_reverse_reference_metadata(
    tmp_path: Path,
):
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "a.md"
    source = tree / "b.md"
    target.write_text("target content", encoding="utf-8")
    source.write_text("source content", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tree)
    with database.connect() as connection:
        target_row = connection.execute(
            "SELECT id, name, path FROM documents WHERE name = 'a.md'"
        ).fetchone()
        source_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'b.md'"
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            (
                f"references:{source_id}",
                json.dumps(
                    [target_row["id"], target_row["name"], target_row["path"], "keep.md"]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            (f"references:{target_row['id']}", json.dumps([source_id])),
        )
        connection.commit()

    target.unlink()
    indexer.index_root(tree)

    with database.connect() as connection:
        rows = {
            row["key"]: json.loads(row["value"])
            for row in connection.execute(
                "SELECT key, value FROM document_graph_metadata"
                " WHERE key LIKE 'references:%'"
            )
        }
    assert f"references:{target_row['id']}" not in rows
    assert rows[f"references:{source_id}"] == ["keep.md"]
    assert source.exists()
    assert not target.exists()


def test_failed_post_commit_invalidation_leaves_a_durable_dirty_marker(
    tmp_path: Path, monkeypatch
):
    from datetime import datetime, timezone
    from unittest.mock import patch
    import hashlib

    from universal_search.domain.document import Document, SourceKind

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("BJT transistor old", encoding="utf-8")
    (tree / "b.md").write_text("BJT transistor old notes", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tree)
    GraphStore(database).rebuild_from_database()
    target = tree / "a.md"
    changed_text = "CMOS MUX changed"
    changed = Document(
        id="a",
        source=SourceKind.LOCAL,
        path=target,
        name=target.name,
        extension=".md",
        size=len(changed_text),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        content=changed_text,
        content_hash=hashlib.sha256(changed_text.encode()).hexdigest(),
    )

    def fail_invalidation(_ids):
        raise RuntimeError("injected graph failure")

    with patch.object(GraphStore, "invalidate", fail_invalidation):
        indexer.upsert(changed)

    with database.connect() as connection:
        dirty = [
            row["key"]
            for row in connection.execute(
                "SELECT key FROM document_graph_metadata WHERE key LIKE 'dirty:%'"
            )
        ]
    assert "dirty:a" in dirty

    # A later lookup repairs the durable marker instead of returning stale data.
    related = GraphStore(database).related("a")
    assert "b" not in {item.document_id for item in related}


def test_reconcile_failure_keeps_a_durable_marker(tmp_path: Path):
    from unittest.mock import patch

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("BJT old", encoding="utf-8")
    (tree / "b.md").write_text("BJT old notes", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(tree)
    GraphStore(database).rebuild_from_database()
    (tree / "a.md").write_text("CMOS new", encoding="utf-8")

    with patch.object(
        GraphStore,
        "invalidate",
        side_effect=RuntimeError("injected reconcile failure"),
    ):
        indexer.index_root(tree)

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_metadata"
            " WHERE key = 'dirty:' || (SELECT id FROM documents WHERE name = 'a.md')"
        ).fetchone()[0] == 1
    with database.connect() as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'a.md'"
        ).fetchone()["id"]
        neighbor_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'b.md'"
        ).fetchone()["id"]
    assert neighbor_id not in {
        item.document_id for item in GraphStore(database).related(document_id)
    }


def test_special_characters_are_parameterized_and_preserved(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record(
                "weird",
                "BJT transistor Ebers-Moll model",
                "quote ' [x] ;.md",
                "docs/with 'quote'/[brackets]/*.md",
            ),
            record(
                "other",
                "BJT transistor Ebers-Moll notes",
                "otro & notes.md",
                "docs/with 'quote'/otro & notes.md",
            ),
        ],
    )
    store.rebuild(records)

    with store.database.connect() as connection:
        paths = [row["path"] for row in connection.execute(
            "SELECT path FROM document_graph_nodes ORDER BY document_id"
        )]
    assert paths == [records[1].path, records[0].path]
    assert store.related("weird")


def test_evidence_contains_signal_names_and_values(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "BJT transistor Ebers-Moll model", "a.md", "notes/a.md"),
            record("b", "BJT transistor Ebers-Moll model", "b.md", "notes/b.md"),
        ],
    )
    store.rebuild(records)

    result = store.related("a")[0]
    kinds = {item.kind for item in result.evidence}
    assert "shared_terms" in kinds
    assert "phrase_overlap" in kinds
    assert all(item.kind and item.kind.isascii() for item in result.evidence)
    assert all(item.values for item in result.evidence)
    assert result.evidence == tuple(
        sorted(result.evidence, key=lambda item: (-item.weight, item.kind, item.values))
    )


def test_related_version_mismatch_is_rebuilt(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "BJT transistor Ebers-Moll model", "a.md", "docs/a.md"),
            record("b", "BJT transistor Ebers-Moll notes", "b.md", "docs/b.md"),
        ],
    )
    store.rebuild(records)
    with store.database.connect() as connection:
        connection.execute(
            "UPDATE document_graph_nodes SET version = version + 1"
        )
        connection.execute(
            "UPDATE document_graph_edges SET version = version + 1"
        )
        connection.commit()

    assert store.related("a")

    with store.database.connect() as connection:
        versions = {
            row["version"]
            for row in connection.execute("SELECT version FROM document_graph_nodes")
        }
    assert versions == {GRAPH_SCHEMA_VERSION}


def test_preprocessing_version_mismatch_rebuilds_rows(tmp_path: Path):
    database = SearchDatabase(tmp_path / "index.db")
    records = [
        record("a", "BJT Ebers-Moll", "a.md"),
        record("b", "BJT Ebers-Moll notes", "b.md"),
    ]
    GraphStore(database, preprocessing_version=1).rebuild(records)

    newer = GraphStore(database, preprocessing_version=2)
    assert newer.related("a")

    with database.connect() as connection:
        versions = {
            row["preprocessing_version"]
            for row in connection.execute("SELECT preprocessing_version FROM document_graph_nodes")
        }
    assert versions == {2}


def test_candidate_generation_is_bounded_and_not_all_pairs(tmp_path: Path):
    records = [
        record(
            f"doc-{index:03d}",
            f"subject{index} alpha{index} beta{index}",
            f"subject{index}.md",
            f"docs/subject{index}.md",
        )
        for index in range(80)
    ]
    store, _ = make_store(tmp_path, records)

    stats = store.rebuild(records)

    assert MAX_GRAPH_TERMS > 0
    assert stats.candidates_considered <= 80 * MAX_CANDIDATES_PER_DOCUMENT
    assert stats.comparisons <= 80 * MAX_CANDIDATES_PER_DOCUMENT
    assert stats.edges_written <= 80 * MAX_EDGES_PER_DOCUMENT
    assert stats.edges_written == 0
    assert len(store.related("doc-000")) == 0


def test_shared_term_postings_and_retrieval_are_bounded(tmp_path: Path):
    records = [
        record(
            f"shared-{index:03d}",
            f"sharedterm subject{index} unique{index}",
            f"subject-{index:03d}.md",
            f"corpus/subject-{index:03d}.md",
        )
        for index in range(200)
    ]
    store, _ = make_store(tmp_path, records)

    stats = store.rebuild(records)

    assert stats.posting_values_read <= (
        len(records) * MAX_GRAPH_TERMS * MAX_POSTINGS_PER_TERM
    )
    assert stats.comparisons <= len(records) * MAX_CANDIDATES_PER_DOCUMENT
    with store.database.connect() as connection:
        shared_count = connection.execute(
            "SELECT COUNT(*) FROM document_graph_terms WHERE term = 'sharedterm'"
        ).fetchone()[0]
    assert shared_count <= MAX_POSTINGS_PER_TERM


def test_alias_and_mention_aggregation_stops_at_hard_cap(tmp_path: Path):
    targets = [
        record(
            f"target-{index:04d}",
            f"unique subject {index}",
            f"target-{index:04d}.md",
            f"corpus/target-{index:04d}.md",
        )
        for index in range(300)
    ]
    mention_text = "See " + " ".join(item.name for item in targets)
    source = DocumentRecord(
        document_id="source",
        name="source.md",
        path="corpus/source.md",
        source="local",
        content_hash="source-hash",
        analysis=analyze(mention_text, name="source.md"),
        content=mention_text,
        references=tuple(item.name for item in targets),
    )
    records = [source, *targets]
    store, _ = make_store(tmp_path, records)

    stats = store.rebuild(records)

    assert stats.alias_candidate_values > 0
    assert stats.alias_candidate_values <= (
        len(records) * MAX_AGGREGATE_CANDIDATE_IDS * 2
    )
    assert stats.candidates_considered <= (
        len(records) * MAX_CANDIDATES_PER_DOCUMENT
    )


def test_related_limit_and_graph_is_not_used_by_search(tmp_path: Path):
    from universal_search.index.search import SearchEngine

    store, records = make_store(
        tmp_path,
        [
            record("a", "BJT transistor model", "a.md", "docs/a.md"),
            record("b", "BJT transistor notes", "b.md", "docs/b.md"),
            record("c", "BJT transistor equations", "c.md", "docs/c.md"),
        ],
    )
    store.rebuild(records)
    # Search remains independent: the graph tables are populated, but the
    # normal query contract and exact filename result do not change.
    for item in records:
        from universal_search.domain.document import Document, SourceKind
        from datetime import datetime, timezone
        import hashlib

        text = item.content or ""
        Indexer(store.database).upsert(
            Document(
                id=item.document_id,
                source=SourceKind.LOCAL,
                path=Path(item.path),
                name=item.name,
                extension=".md",
                size=len(text),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                content=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )

    assert [item.document_id for item in store.related("a", limit=1)] == ["b"]
    assert store.related("a", limit=0) == []
    results = SearchEngine(store.database).search("BJT")
    assert any(result.name == "a.md" for result in results)


def test_node_and_edge_generation_columns_are_current(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "CMOS logic", "a.md"),
            record("b", "CMOS logic", "b.md"),
        ],
    )
    store.rebuild(records)
    with store.database.connect() as connection:
        nodes = connection.execute("SELECT * FROM document_graph_nodes").fetchall()
        edges = connection.execute("SELECT * FROM document_graph_edges").fetchall()
        terms = connection.execute("SELECT * FROM document_graph_terms").fetchall()
    assert nodes and edges and terms
    for row in [*nodes, *edges, *terms]:
        assert row["version"] == GRAPH_SCHEMA_VERSION
        assert row["generation"] == rows_generation(store)


def rows_generation(store: GraphStore) -> str:
    with store.database.connect() as connection:
        row = connection.execute(
            "SELECT generation FROM document_graph_nodes ORDER BY document_id LIMIT 1"
        ).fetchone()
    return str(row["generation"])


def test_rebuild_accepts_a_generator_and_reports_json(tmp_path: Path):
    store, records = make_store(
        tmp_path,
        [
            record("a", "BJT Ebers-Moll", "a.md"),
            record("b", "BJT Ebers-Moll notes", "b.md"),
        ],
    )
    stats = store.rebuild(record for record in records)
    payload = stats.as_dict()
    assert payload["nodes_written"] == 2
    assert json.dumps(payload)
