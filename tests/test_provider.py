import hashlib
from pathlib import Path

import pytest

from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.base import (
    AVAILABILITY_AVAILABLE,
    CancelToken,
    DocumentProvider,
    ProviderError,
    ProviderFile,
)
from universal_search.providers.local import LocalProvider, discover_local


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
        # The full provider contract of spec 019: a key, a version, the
        # capabilities it offers and an availability answer.
        key = "memory"
        version = "1.0"
        capabilities = frozenset({"enumerate", "metadata", "content", "identity"})

        def __init__(self, documents: list[Document]) -> None:
            self._documents = documents

        def available(self) -> bool:
            return True

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


# -- phase 024: the streaming provider contract ----------------------------------


def test_local_provider_iter_files_yields_namespaced_files(tmp_path: Path) -> None:
    nested = tmp_path / "projects"
    nested.mkdir()
    file = nested / "note.md"
    file.write_text("oscilloscope calibration", encoding="utf-8")

    items = [
        item for item in LocalProvider().iter_files(tmp_path)
        if isinstance(item, ProviderFile)
    ]

    assert len(items) == 1
    (item,) = items
    assert item.provider == "local"
    assert item.path == file.resolve()
    assert item.size == file.stat().st_size
    assert item.availability == AVAILABILITY_AVAILABLE
    assert item.document_id == document_id_for("local", file.resolve())


def test_local_provider_iter_files_reports_unscannable_roots(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory.md"
    target.write_text("soy un fichero", encoding="utf-8")

    items = list(LocalProvider().iter_files(target))

    assert len(items) == 1
    (error,) = items
    assert isinstance(error, ProviderError)
    assert error.provider == "local"
    assert error.path == target.resolve()


def test_local_provider_iter_files_stops_at_cancel_token(tmp_path: Path) -> None:
    for number in range(20):
        (tmp_path / f"f{number:02d}.md").write_text(f"contenido {number}", encoding="utf-8")
    token = CancelToken()

    iterator = LocalProvider().iter_files(tmp_path, token)
    next(iterator)
    token.cancel()
    with pytest.raises(StopIteration):
        next(iterator)


def test_local_provider_iter_files_skips_ignored_paths(tmp_path: Path) -> None:
    from universal_search.providers.base import IgnoredPath

    (tmp_path / "keep.md").write_text("contenido util", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secreto.md").write_text("contenido oculto", encoding="utf-8")

    files = [
        item for item in LocalProvider().iter_files(tmp_path)
        if isinstance(item, ProviderFile)
    ]
    ignored = [
        item for item in LocalProvider().iter_files(tmp_path)
        if isinstance(item, IgnoredPath)
    ]

    assert [item.path.name for item in files] == ["keep.md"]
    assert [item.path.name for item in ignored] == [".git"]


def test_local_provider_satisfies_the_provider_protocol() -> None:
    from universal_search.providers.base import Provider

    assert isinstance(LocalProvider(), Provider)
