import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from universal_search.domain.document import Document, SourceKind
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


def make_document(path: Path, content: str) -> Document:
    """Build a document the way a provider would, with an id derived from its state."""
    return Document(
        id=hashlib.sha256(f"{path}|{content}".encode()).hexdigest(),
        source=SourceKind.LOCAL,
        path=path,
        name=path.name,
        extension=path.suffix.lower(),
        size=len(content),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def count_rows(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_upsert_makes_document_searchable(tmp_path: Path) -> None:
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).upsert(make_document(Path("C:/docs/report.md"), "needle in a haystack"))

    results = SearchEngine(database).search("needle")

    assert len(results) == 1
    assert results[0].source == "local"


def test_upsert_replaces_stale_full_text_rows(tmp_path: Path) -> None:
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    path = Path("C:/docs/notes.md")
    indexer.upsert(make_document(path, "first revision with keyword alpha"))
    indexer.upsert(make_document(path, "second revision with keyword beta"))

    assert count_rows(database.path, "documents") == 1
    assert count_rows(database.path, "documents_fts") == 1

    assert len(SearchEngine(database).search("beta")) == 1
    assert SearchEngine(database).search("alpha") == []


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    document = make_document(Path("C:/docs/notes.md"), "same content twice")
    indexer.upsert(document)
    indexer.upsert(document)

    assert count_rows(database.path, "documents") == 1
    assert count_rows(database.path, "documents_fts") == 1
