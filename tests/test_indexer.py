import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.base import INTERFACE_VERSION, CancelToken, ProviderFile


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


# -- phase 024: (source, path) identity and source-scoped reconciliation ---------


def fake_provider(key: str, files: list[Path]) -> "FakeFilesProvider":
    return FakeFilesProvider(key, files)


class FakeFilesProvider:
    """Minimal phase-024 provider over a fixed set of real files."""

    def __init__(self, key: str, files: list[Path]) -> None:
        self.key = key
        self.version = "1.0"
        self.interface_version = INTERFACE_VERSION
        self.capabilities = frozenset({"enumerate", "metadata", "identity"})
        self._files = files

    def available(self) -> bool:
        return True

    def iter_files(self, root: Path, cancel=None):
        for path in self._files:
            stat = path.stat()
            yield ProviderFile(
                provider=self.key,
                path=path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                created_at=None,
                modified_at=None,
            )


def test_upsert_scopes_previous_lookup_by_source(tmp_path: Path) -> None:
    """The same path owned by two providers is two documents, not a clash."""
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    path = Path("C:/docs/report.md")
    indexer.upsert(make_document(path, "local copy"))
    indexer.upsert(
        replace(
            make_document(path, "nas copy"),
            id=document_id_for("network", path),
            source=SourceKind.NETWORK,
        )
    )

    assert count_rows(database.path, "documents") == 2
    assert count_rows(database.path, "documents_fts") == 2
    assert {r.source for r in SearchEngine(database).search("copy")} == {
        "local", "network"
    }


def test_index_root_with_provider_deletes_only_that_providers_rows(
    tmp_path: Path,
) -> None:
    """Reconciling one provider must not delete another provider's rows."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "nota.md").write_text("contenido compartido", encoding="utf-8")
    (shared / "viejo.md").write_text("contenido viejo", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(shared, provider=fake_provider("fake-a", [shared / "nota.md"]))
    indexer.index_root(shared, provider=fake_provider("fake-b", [shared / "nota.md"]))

    # A new pass of fake-a that no longer sees 'viejo.md'... 'viejo.md' was
    # never indexed; instead re-index fake-b with a different file set.
    (shared / "nuevo.md").write_text("contenido nuevo", encoding="utf-8")
    stats = indexer.index_root(
        shared, provider=fake_provider("fake-b", [shared / "nota.md", shared / "nuevo.md"])
    )

    assert stats.deleted == 0  # fake-a's row is untouched by fake-b's pass
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT source, name FROM documents ORDER BY source, name"
        ).fetchall()
    assert [(row["source"], row["name"]) for row in rows] == [
        ("fake-a", "nota.md"),
        ("fake-b", "nota.md"),
        ("fake-b", "nuevo.md"),
    ]


def test_deletion_is_scoped_when_a_provider_owns_the_root(
    tmp_path: Path,
) -> None:
    """A file that disappears is removed only from its own provider."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "nota.md").write_text("contenido compartido", encoding="utf-8")
    (shared / "temporal.md").write_text("contenido temporal", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(shared, provider=fake_provider("fake-a", [shared / "nota.md"]))
    indexer.index_root(
        shared,
        provider=fake_provider("fake-b", [shared / "nota.md", shared / "temporal.md"]),
    )

    (shared / "temporal.md").unlink()
    stats = indexer.index_root(shared, provider=fake_provider("fake-b", [shared / "nota.md"]))

    assert stats.deleted == 1
    assert SearchEngine(database).search("temporal") == []
    assert [r.name for r in SearchEngine(database).search("compartido")] == ["nota.md", "nota.md"]


class CancelAfterProvider:
    """Yields a few files, then cooperatively cancels mid-enumeration.

    The cancellation is cooperative: the provider stops without raising, so
    the indexer's loop ends normally. A correct indexer must still treat the
    pass as incomplete and skip the deletion pass.
    """

    key = "fake"
    version = "1.0"
    interface_version = INTERFACE_VERSION
    capabilities = frozenset({"enumerate", "metadata", "identity"})

    def __init__(self, files: list[Path], cancel, yield_count: int = 2) -> None:
        self._files = list(files)
        self._cancel = cancel
        self._yield_count = yield_count

    def available(self) -> bool:
        return True

    def iter_files(self, root: Path, cancel=None):
        for index, path in enumerate(self._files):
            if index >= self._yield_count:
                if cancel is not None:
                    cancel.cancel()
                return
            stat = path.stat()
            yield ProviderFile(
                provider=self.key,
                path=path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                created_at=None,
                modified_at=None,
            )


def test_cancelled_provider_pass_never_deletes_unreached_rows(
    tmp_path: Path,
) -> None:
    """A cooperative cancel mid-enumeration must skip the deletion pass.

    Pre-existing rows under the root that the cancelled pass never reached
    must survive: the pass did not see them, so it must not delete them.
    """
    root = tmp_path / "root"
    files = []
    for number in range(5):
        path = root / f"f{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"contenido {number}", encoding="utf-8")
        files.append(path)
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(root, provider=fake_provider("fake", files))

    token = CancelToken()
    stats = indexer.index_root(
        root,
        provider=CancelAfterProvider(files, token, yield_count=2),
        cancel=token,
    )

    assert stats.deleted == 0
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]
    # All five pre-existing rows survive; the three unreached were not deleted.
    assert count == 5


def test_provider_pass_does_not_touch_other_providers_rows(
    tmp_path: Path,
) -> None:
    """A provider pass must not read or re-extract another provider's row.

    With two providers owning the same path, the second provider's stored
    lookup must be scoped by source: it must not find the first provider's
    row, re-extract the content, or add the first provider's id to
    graph_touched (which would invalidate the first provider's graph node).
    """
    from universal_search.providers.local import read_local_content

    shared = tmp_path / "shared"
    real = shared / "nota.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("contenido compartido", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(shared, provider=fake_provider("fake-a", [real]))

    reads: list[Path] = []

    def counting_read(path: Path):
        reads.append(path)
        return read_local_content(path)

    stats = indexer.index_root(
        shared,
        provider=fake_provider("fake-b", [real]),
        read_content=counting_read,
    )

    # B's row is created through the clean path (stored is None), not
    # "updated" via A's row — the updated path is the one that adds A's id
    # to graph_touched.
    assert stats.created == 1
    assert stats.updated == 0
    assert len(reads) == 1  # exactly one read: B's own creation
    # Both providers' copies are searchable.
    assert len(SearchEngine(database).search("compartido")) == 2
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT source FROM documents ORDER BY source"
        ).fetchall()
    assert [row["source"] for row in rows] == ["fake-a", "fake-b"]
