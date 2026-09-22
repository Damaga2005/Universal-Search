import hashlib
import os
from pathlib import Path

from universal_search.domain.document import document_id_for
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.ignore import IgnoreRules
from universal_search.providers.local import discover_local


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def index(database: SearchDatabase, root: Path, **kwargs):
    return Indexer(database).index_root(root, **kwargs)


# -- created / modified / renamed / deleted --------------------------------


def test_create_then_index(tmp_path: Path, row_count) -> None:
    root = write(tmp_path / "files", "notes.md", "fresh idea about capacitors")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = index(database, tmp_path / "files")

    assert stats.created == 1
    assert stats.deleted == 0
    assert row_count(database.path, "documents") == 1
    assert row_count(database.path, "documents_fts") == 1
    assert len(SearchEngine(database).search("capacitors")) == 1
    assert root.exists()


def test_modify_then_reindex(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    target = write(files, "notes.md", "version one keyword alpha")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    target.write_text("version two keyword beta", encoding="utf-8")
    os.utime(target, ns=(1, 1))  # force a different mtime
    stats = index(database, files)

    assert stats.updated == 1
    assert stats.created == 0
    assert row_count(database.path, "documents") == 1
    assert row_count(database.path, "documents_fts") == 1
    engine = SearchEngine(database)
    assert len(engine.search("beta")) == 1
    assert engine.search("alpha") == []


def test_rename_replaces_old_path(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    old = write(files, "before.md", "renamed document keyword")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    new = files / "after.md"
    old.rename(new)
    stats = index(database, files)

    assert stats.created == 1
    assert stats.deleted == 1
    assert row_count(database.path, "documents") == 1
    assert row_count(database.path, "documents_fts") == 1
    engine = SearchEngine(database)
    results = engine.search("keyword")
    assert [r.name for r in results] == ["after.md"]


def test_delete_removes_document_and_fts_rows(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    target = write(files, "gone.md", "temporary secretvalue")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    target.unlink()
    stats = index(database, files)

    assert stats.deleted == 1
    assert row_count(database.path, "documents") == 0
    assert row_count(database.path, "documents_fts") == 0
    assert SearchEngine(database).search("secretvalue") == []


# -- repeated passes --------------------------------------------------------


def test_second_run_is_unchanged_and_adds_nothing(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    write(files, "a.txt", "alpha content")
    write(files, "nested/b.md", "beta content")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    first = index(database, files)
    documents_after_first = row_count(database.path, "documents")

    second = index(database, files)
    third = index(database, files)

    assert first.created == 2
    assert second.created == 0 and second.updated == 0 and second.deleted == 0
    assert second.unchanged == 2
    assert third.unchanged == 2
    assert row_count(database.path, "documents") == documents_after_first
    assert row_count(database.path, "documents_fts") == documents_after_first


def test_second_run_does_not_read_content(tmp_path: Path) -> None:
    files = tmp_path / "files"
    write(files, "a.txt", "alpha content")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    reads: list[Path] = []

    def tracking_reader(path: Path):
        from universal_search.domain.extraction import ExtractionResult

        reads.append(path)
        return ExtractionResult(text="should not be used")

    index(database, files, read_content=tracking_reader)

    assert reads == []


def test_identical_content_with_new_mtime_is_unchanged(
    tmp_path: Path, row_count
) -> None:
    files = tmp_path / "files"
    target = write(files, "stable.md", "content stays exactly the same")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    os.utime(target, ns=(999, 999))  # mtime changes, bytes do not
    stats = index(database, files)

    assert stats.unchanged == 1
    assert stats.updated == 0
    assert row_count(database.path, "documents") == 1
    assert row_count(database.path, "documents_fts") == 1
    # The stored mtime was refreshed so the next pass takes the fast path.
    import sqlite3

    connection = sqlite3.connect(database.path)
    mtime_ns = connection.execute(
        "SELECT mtime_ns FROM documents WHERE path = ?", (str(target),)
    ).fetchone()[0]
    connection.close()
    assert mtime_ns == target.stat().st_mtime_ns
    assert len(SearchEngine(database).search("exactly")) == 1


def test_changed_content_updates_hash(tmp_path: Path) -> None:
    files = tmp_path / "files"
    target = write(files, "doc.md", "first body")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, files)

    target.write_text("second body", encoding="utf-8")
    os.utime(target, ns=(2, 2))
    index(database, files)

    import sqlite3

    connection = sqlite3.connect(database.path)
    stored_hash = connection.execute(
        "SELECT content_hash FROM documents WHERE path = ?", (str(target),)
    ).fetchone()[0]
    connection.close()
    assert stored_hash == hashlib.sha256(b"second body").hexdigest()


# -- ignore rules -----------------------------------------------------------


def test_ignored_directory_and_file_are_not_indexed(tmp_path: Path) -> None:
    files = tmp_path / "files"
    write(files, "keep.md", "visible content")
    write(files, "node_modules/lib/index.js", "hidden dependency")
    write(files, "scratch.tmp", "temporary junk")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = index(database, files)

    assert stats.created == 1
    assert stats.ignored == 2
    engine = SearchEngine(database)
    assert len(engine.search("visible")) == 1
    assert engine.search("dependency") == []
    assert engine.search("junk") == []


def test_ignore_rules_are_configurable(tmp_path: Path) -> None:
    files = tmp_path / "files"
    write(files, "normal.md", "ordinary note")
    write(files, "private/secret.md", "classified note")
    write(files, "draft.off", "unfinished note")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    rules = IgnoreRules.defaults(directories=("private",), patterns=("*.off",))
    stats = index(database, files, rules=rules)

    assert stats.created == 1
    engine = SearchEngine(database)
    assert len(engine.search("ordinary")) == 1
    assert engine.search("classified") == []
    assert engine.search("unfinished") == []


def test_index_database_is_never_indexed(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    write(files, "note.md", "a real note")
    database = SearchDatabase(files / "search.db")  # database lives inside the root

    stats = index(database, files)

    assert stats.created == 1
    assert stats.ignored >= 1  # the .db itself
    paths = {
        row[0]
        for row in _fetch_paths(database)
    }
    assert str(database.path) not in paths
    assert not any(p.endswith(("-wal", "-shm")) for p in paths)


def _fetch_paths(database: SearchDatabase):
    import sqlite3

    connection = sqlite3.connect(database.path)
    try:
        return connection.execute("SELECT path FROM documents").fetchall()
    finally:
        connection.close()


# -- inaccessible files -----------------------------------------------------


def test_unreadable_file_is_counted_and_run_continues(tmp_path: Path, row_count) -> None:
    files = tmp_path / "files"
    write(files, "locked.md", "cannot read me")
    write(files, "fine.md", "readable content")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    from universal_search.domain.extraction import ExtractionResult

    def failing_reader(path: Path) -> ExtractionResult:
        if path.name == "locked.md":
            raise PermissionError("access denied")
        from universal_search.providers.local import read_local_content

        return read_local_content(path)

    stats = index(database, files, read_content=failing_reader)

    assert stats.extraction_errors == 1
    assert stats.errors == 0
    assert stats.created == 2  # metadata for the locked file is still indexed
    engine = SearchEngine(database)
    assert len(engine.search("readable")) == 1
    # The file remains findable by name even though its content failed.
    assert [r.name for r in engine.search("locked")] == ["locked.md"]


def test_inaccessible_directory_is_counted_and_run_continues(
    tmp_path: Path, monkeypatch
) -> None:
    files = tmp_path / "files"
    write(files, "ok.md", "healthy content")
    (files / "locked-dir").mkdir()
    database = SearchDatabase(tmp_path / "index" / "search.db")

    import universal_search.providers.local as local_module

    real_scandir = os.scandir

    def guarded_scandir(path):
        if "locked-dir" in str(path):
            raise PermissionError("access denied")
        return real_scandir(path)

    monkeypatch.setattr(local_module.os, "scandir", guarded_scandir)

    stats = index(database, files)

    assert stats.errors == 1
    assert stats.created == 1
    assert len(SearchEngine(database).search("healthy")) == 1


# -- multiple providers and roots ------------------------------------------


def test_second_root_survives_reconciliation_of_first(tmp_path: Path, row_count) -> None:
    root_a = write(tmp_path / "a", "one.md", "first root content")
    write(tmp_path / "b", "two.md", "second root content")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, tmp_path / "a")
    index(database, tmp_path / "b")

    root_a.unlink()  # delete from root A only
    stats = index(database, tmp_path / "a")

    assert stats.deleted == 1
    engine = SearchEngine(database)
    assert engine.search("first") == []
    assert len(engine.search("second")) == 1
    assert row_count(database.path, "documents") == 1


def test_other_source_documents_survive_local_reconciliation(tmp_path: Path) -> None:
    """A local reconcile must never touch documents from other providers."""
    from universal_search.domain.document import Document, SourceKind

    files = write(tmp_path / "files", "note.md", "local note")
    database = SearchDatabase(tmp_path / "index" / "search.db")
    index(database, tmp_path / "files")

    foreign = Document(
        id=document_id_for(SourceKind.OTHER, "memory://shared/agenda.md"),
        source=SourceKind.OTHER,
        path=Path("memory://shared/agenda.md"),
        name="agenda.md",
        extension=".md",
        size=10,
        created_at=None,
        modified_at=None,
        content="quarterly budget",
        content_hash=None,
    )
    Indexer(database).upsert(foreign)

    files.unlink()
    index(database, tmp_path / "files")

    engine = SearchEngine(database)
    assert engine.search("local") == []
    assert [r.name for r in engine.search("budget")] == ["agenda.md"]


# -- stats and identity -----------------------------------------------------


def test_stats_summary_reports_every_counter(tmp_path: Path) -> None:
    files = tmp_path / "files"
    write(files, "keep.md", "content")
    write(files, "skip.tmp", "junk")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    stats = index(database, files)

    summary = stats.summary()
    for part in (
        "created=1", "updated=0", "unchanged=0", "deleted=0",
        "ignored=1", "errors=0", "extraction_errors=0",
    ):
        assert part in summary
    assert stats.as_dict()["created"] == 1
    assert stats.scanned == 1


def test_document_identity_is_stable_across_content_changes() -> None:
    first = document_id_for("local", "C:/docs/report.md")
    second = document_id_for("local", "C:/docs/report.md")

    assert first == second
    assert first != document_id_for("local", "C:/docs/other.md")
    assert first != document_id_for("other", "C:/docs/report.md")


def test_discover_local_and_index_root_agree_on_identity(tmp_path: Path) -> None:
    files = tmp_path / "files"
    write(files, "note.md", "shared identity")
    database = SearchDatabase(tmp_path / "index" / "search.db")

    index(database, files)
    document = next(iter(discover_local(files)))

    import sqlite3

    connection = sqlite3.connect(database.path)
    stored_id = connection.execute(
        "SELECT id FROM documents WHERE path = ?", (str(document.path),)
    ).fetchone()[0]
    connection.close()
    assert stored_id == document.id
