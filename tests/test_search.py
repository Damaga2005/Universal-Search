from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.local import discover_local


def test_local_document_is_searchable(tmp_path: Path) -> None:
    document = tmp_path / "bjt_notes.md"
    document.write_text("# BJT\nEbers-Moll model and transistor operating point.", encoding="utf-8")
    db = SearchDatabase(tmp_path / "search.db")
    indexer = Indexer(db)
    for item in discover_local(tmp_path):
        indexer.upsert(item)
    results = SearchEngine(db).search("BJT")
    assert results
    assert results[0].name == "bjt_notes.md"
    assert "BJT" in (results[0].snippet or "")
