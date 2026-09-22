import hashlib
from pathlib import Path

from universal_search.domain.document import Document, SourceKind
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.base import DocumentProvider
from universal_search.providers.local import discover_local


def test_discover_local_builds_documents(tmp_path: Path) -> None:
    nested = tmp_path / "projects" / "lab"
    nested.mkdir(parents=True)
    file = nested / "note.md"
    file.write_text("oscilloscope calibration", encoding="utf-8")

    documents = list(discover_local(tmp_path))

    assert len(documents) == 1
    document = documents[0]
    assert document.source is SourceKind.LOCAL
    assert document.path == file.resolve()
    assert document.name == "note.md"
    assert document.extension == ".md"
    assert document.size == file.stat().st_size
    assert document.created_at is not None
    assert document.modified_at is not None
    assert document.content == "oscilloscope calibration"
    assert document.content_hash == hashlib.sha256(b"oscilloscope calibration").hexdigest()


def test_discover_local_on_empty_directory_yields_nothing(tmp_path: Path) -> None:
    assert list(discover_local(tmp_path)) == []


def test_byte_order_mark_is_stripped_from_content(tmp_path: Path) -> None:
    (tmp_path / "bom.md").write_bytes(b"\xef\xbb\xbf# heading")

    documents = list(discover_local(tmp_path))

    assert len(documents) == 1
    assert documents[0].content == "# heading"


def test_non_text_file_is_indexed_without_content(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "logo.png").write_bytes(b"\x89PNG secretword \x00\x01")

    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    for item in discover_local(files):
        indexer.upsert(item)
    engine = SearchEngine(database)

    assert engine.search("secretword") == []
    assert [r.name for r in engine.search("logo")] == ["logo.png"]


def test_custom_provider_documents_are_searchable(tmp_path: Path) -> None:
    """Providers only produce domain documents; the search engine never sees them."""

    class MemoryProvider:
        def __init__(self, documents: list[Document]) -> None:
            self._documents = documents

        def discover(self, root: Path) -> list[Document]:
            return list(self._documents)

    provider = MemoryProvider(
        [
            Document(
                id="doc-42",
                source=SourceKind.OTHER,
                path=Path("memory://shared/agenda.md"),
                name="agenda.md",
                extension=".md",
                size=21,
                created_at=None,
                modified_at=None,
                content="budget review meeting",
                content_hash=None,
            )
        ]
    )
    assert isinstance(provider, DocumentProvider)

    database = SearchDatabase(tmp_path / "index.db")
    for document in provider.discover(Path("/")):
        Indexer(database).upsert(document)

    results = SearchEngine(database).search("budget")

    assert len(results) == 1
    assert results[0].source == "other"
    assert results[0].name == "agenda.md"
