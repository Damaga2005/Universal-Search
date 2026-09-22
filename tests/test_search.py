from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.local import discover_local


def build_index(root: Path, db_path: Path) -> SearchDatabase:
    database = SearchDatabase(db_path)
    indexer = Indexer(database)
    for item in discover_local(root):
        indexer.upsert(item)
    return database


def test_local_document_is_searchable_by_content(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    document = files / "bjt_notes.md"
    document.write_text("# BJT\nEbers-Moll model and transistor operating point.", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")
    results = SearchEngine(database).search("BJT")

    assert results
    assert results[0].name == "bjt_notes.md"
    assert "BJT" in (results[0].snippet or "")


def test_search_matches_file_name_of_binary_file(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "annual_report_2026.pdf").write_bytes(b"%PDF-1.7 \x00\x01 binary")

    database = build_index(files, tmp_path / "index" / "search.db")
    results = SearchEngine(database).search("annual")

    assert [r.name for r in results] == ["annual_report_2026.pdf"]


def test_search_matches_path_components(tmp_path: Path) -> None:
    files = tmp_path / "files"
    nested = files / "projects" / "physics"
    nested.mkdir(parents=True)
    (nested / "derivation.txt").write_text("schrodinger equation", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")
    results = SearchEngine(database).search("physics")

    assert [r.name for r in results] == ["derivation.txt"]


def test_result_exposes_source_path_name_and_snippet(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    document = files / "discharge_notes.md"
    document.write_text("capacitor discharge through a resistor", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")
    results = SearchEngine(database).search("capacitor")

    assert len(results) == 1
    result = results[0]
    assert result.source == "local"
    assert result.path == document.resolve()
    assert result.name == "discharge_notes.md"
    assert "capacitor" in (result.snippet or "")


def test_empty_query_returns_no_results(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "note.txt").write_text("some words", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")

    assert SearchEngine(database).search("") == []
    assert SearchEngine(database).search("   ") == []


def test_database_is_created_automatically(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh" / "auto.db"
    assert not db_path.exists()

    results = SearchEngine(SearchDatabase(db_path)).search("anything")

    assert results == []
    assert db_path.exists()


def test_query_with_unbalanced_quote_falls_back_to_plain_terms(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "review.txt").write_text("quarterly notes for review", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")
    results = SearchEngine(database).search('"notes')

    assert [r.name for r in results] == ["review.txt"]


def test_symbol_only_query_returns_no_results(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "note.txt").write_text("some words", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")

    symbol_queries = ['"', "*", "()"]
    for query in symbol_queries:
        assert SearchEngine(database).search(query) == []


def test_search_respects_limit(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (files / name).write_text("redundant content", encoding="utf-8")

    database = build_index(files, tmp_path / "index" / "search.db")

    assert len(SearchEngine(database).search("redundant", limit=2)) == 2
