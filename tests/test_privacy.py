"""Privacy and security regressions (spec 018).

Each test corresponds to a threat in `docs/PRIVACY.md`. The point is not
to re-prove that SQLite is injection-safe: it is to make sure a future
change cannot quietly undo a property the application depends on.
"""

import json
import logging
import os
import sys
from contextlib import closing
from pathlib import Path

import pytest

from universal_search.appconfig import AppPaths, setup_logging
from universal_search.extractors.text import MAX_CONTENT_CHARS
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.intelligence import analyze, rebuild
from universal_search.privacy import INVENTORY, forget, inventory_report
from universal_search.providers.base import ProviderFile


def build(root: Path) -> SearchDatabase:
    tree = root / "tree"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "nota.md").write_text(
        "documento con transistor bjt", encoding="utf-8"
    )
    (tree / "privado").mkdir()
    (tree / "privado" / "secreto.txt").write_text(
        "informacion muy privada", encoding="utf-8"
    )
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(tree)
    return database


@pytest.fixture
def indexed(tmp_path: Path) -> SearchDatabase:
    return build(tmp_path)


# -- the data inventory --------------------------------------------------------

def test_inventory_is_complete_and_never_leaves_the_machine():
    assert INVENTORY
    for item in INVENTORY:
        assert item.what and item.where and item.purpose
        assert item.retention and item.deletion
        assert item.leaves_machine is False
    keys = {item.key for item in INVENTORY}
    graph_item = next(item for item in INVENTORY if item.key == "relationship_graph")
    assert "document_graph_metadata" in graph_item.where
    # Everything the application actually persists must be declared.
    assert {
        "documents", "content", "intelligence", "relationship_graph", "usage",
        "recents", "control_state", "logs", "metrics", "process_coordination",
    } <= keys


def test_inventory_report_measures_the_real_locations(indexed, tmp_path: Path):
    report = inventory_report(indexed, AppPaths.discover(home=tmp_path / "home"))
    assert report["leaves_machine"] is False
    assert report["bytes"]["index"] > 0
    assert report["application_home"].endswith("home")
    assert len(report["items"]) == len(INVENTORY)


def test_inventory_measures_persistent_process_coordination_files(
    indexed,
    tmp_path: Path,
):
    paths = AppPaths.discover(home=tmp_path / "home")
    paths.ensure()
    paths.tray_pid_file.write_text("1234", encoding="ascii")
    paths.tray_pid_file.with_name("tray.pid.lock").write_bytes(b"\0")
    paths.diagnostics_request_file.touch()
    paths.lock_file.write_text("1234", encoding="ascii")
    paths.worker_lease_file.write_bytes(b"\0")
    paths.worker_owner_file.write_text(
        '{"pid": 1234, "generation": "generation-a"}',
        encoding="utf-8",
    )

    report = inventory_report(indexed, paths)

    assert report["bytes"]["coordination"] > 0
    coordination = next(
        item for item in report["items"] if item["key"] == "process_coordination"
    )
    assert "pid" in coordination["what"].casefold()
    assert "document" in coordination["what"].casefold()
    assert "delete" in coordination["deletion"].lower()


def test_inventory_measures_generation_scoped_coordination_files(
    indexed,
    tmp_path: Path,
):
    paths = AppPaths.discover(home=tmp_path / "home")
    paths.ensure()
    before = inventory_report(indexed, paths)["bytes"]["coordination"]
    startup_claim = paths.startup_claim_file_for(1234, "generation-a")
    startup_claim.write_text("starter", encoding="ascii")
    startup_temp = paths.startup_claim_temporary_file_for(
        1234,
        "generation-crashed",
    )
    startup_temp.write_text("partial", encoding="ascii")
    stop_request = paths.stop_request_file("generation-a")
    stop_request.write_bytes(b"stop")

    after = inventory_report(indexed, paths)["bytes"]["coordination"]

    assert after == before + len(b"starter") + len(b"partial") + len(b"stop")


def test_no_network_module_is_imported_by_the_application():
    """Local-only, proven by absence rather than by documentation."""
    forbidden = ("socket", "http", "urllib", "requests", "ftplib", "smtplib")
    package = Path(__file__).resolve().parents[1] / "src" / "universal_search"
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in forbidden:
            if f"import {module}" in text:
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


# -- deletion ------------------------------------------------------------------

def test_forget_removes_the_document_and_everything_derived(
    indexed: SearchDatabase, tmp_path: Path
):
    target = tmp_path / "tree" / "privado" / "secreto.txt"
    rebuild(indexed)
    with closing(indexed.connect()) as connection:
        target_id = connection.execute(
            "SELECT id FROM documents WHERE path = ?", (str(target),)
        ).fetchone()["id"]
    with closing(indexed.connect()) as connection:
        connection.execute(
            "INSERT INTO usage_events(document_id, query)"
            " SELECT id, ? FROM documents WHERE path = ?",
            ("consulta privada", str(target)),
        )
        connection.commit()

    result = forget(indexed, target)

    assert result.documents == 1
    assert result.search_rows == 1
    assert result.derived_rows == 1
    assert result.usage_rows == 1
    # The file itself is untouched: this is "stop indexing", not "delete".
    assert target.exists()
    # And it is gone from search, derived data and usage.
    assert SearchEngine(indexed).search("privada") == []
    with indexed.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM usage_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_intelligence"
            " WHERE document_id NOT IN (SELECT id FROM documents)"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_nodes"
            " WHERE document_id NOT IN (SELECT id FROM documents)"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_edges"
            " WHERE source_document_id NOT IN (SELECT id FROM documents)"
            " OR target_document_id NOT IN (SELECT id FROM documents)"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_metadata WHERE key IN (?, ?)",
            (f"dirty:{target_id}", f"references:{target_id}"),
        ).fetchone()[0] == 0


def test_forget_scrubs_direct_and_reverse_reference_metadata(
    indexed: SearchDatabase, tmp_path: Path
):
    target = tmp_path / "tree" / "privado" / "secreto.txt"
    source = tmp_path / "tree" / "docs" / "nota.md"
    with closing(indexed.connect()) as connection:
        target_row = connection.execute(
            "SELECT id, name, path FROM documents WHERE path = ?", (str(target),)
        ).fetchone()
        source_id = connection.execute(
            "SELECT id FROM documents WHERE path = ?", (str(source),)
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            (
                f"references:{source_id}",
                json.dumps(
                    [
                        target_row["id"],
                        target_row["name"],
                        target_row["path"],
                        "keep.md",
                    ]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            (f"references:{target_row['id']}", json.dumps([source_id])),
        )
        connection.commit()

    forget(indexed, target)

    with closing(indexed.connect()) as connection:
        rows = {
            row["key"]: json.loads(row["value"])
            for row in connection.execute(
                "SELECT key, value FROM document_graph_metadata"
                " WHERE key LIKE 'references:%'"
            )
        }
    assert f"references:{target_row['id']}" not in rows
    assert rows[f"references:{source_id}"] == ["keep.md"]
    assert target.exists()
    assert source.exists()


def test_forget_accepts_a_name_and_reports_an_unknown_path(
    indexed: SearchDatabase, tmp_path: Path
):
    assert forget(indexed, "secreto.txt").total >= 1
    empty = forget(indexed, tmp_path / "nunca" / "indexado.txt")
    assert empty.total == 0


class _TwoProviderDoc:
    """Minimal provider over one real file, to index the same path twice."""

    def __init__(self, key: str, path: Path) -> None:
        self.key = key
        self.version = "1.0"
        self.interface_version = 2
        self.capabilities = frozenset({"enumerate", "metadata", "identity"})
        self._path = path

    def available(self) -> bool:
        return True

    def iter_files(self, root: Path, cancel=None):
        stat = self._path.stat()
        yield ProviderFile(
            provider=self.key,
            path=self._path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            created_at=None,
            modified_at=None,
        )


def test_forget_removes_every_provider_copy_of_a_path(tmp_path: Path):
    """Two providers owning the same path: forget must remove both copies.

    Phase 024 allows two providers to own the same path. A path-only
    forget() would delete one row and leave the other searchable — a
    privacy leak. forget() must delete every row for the path and all
    derived data.
    """
    from universal_search.privacy import forget

    shared = tmp_path / "shared"
    real = shared / "nota.md"
    real.parent.mkdir(parents=True)
    real.write_text("contenido privado", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)
    indexer.index_root(shared, provider=_TwoProviderDoc("fake-a", real))
    indexer.index_root(shared, provider=_TwoProviderDoc("fake-b", real))

    result = forget(database, real)

    assert result.documents == 2
    assert result.search_rows == 2
    assert SearchEngine(database).search("privado") == []
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM documents_fts"
        ).fetchone()[0] == 0


def test_full_deletion_and_rebuild_keeps_the_application_usable(
    indexed: SearchDatabase, tmp_path: Path
):
    from universal_search.diagnostics import rebuild_all

    result = rebuild_all(indexed, [tmp_path / "tree"], confirm=True)
    assert result.changed == 2
    assert [r.name for r in SearchEngine(indexed).search("bjt")]


# -- hostile input -------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile",
    [
        "'); DROP TABLE documents; --",
        "bjt\" OR \"1\"=\"1",
        "1=1) OR (documents_fts MATCH \"x",
        "%00%01 OR _",
        "../../etc/passwd",
        "C:\\Windows\\System32\\config\\SAM",
        "%LOCALAPPDATA%\\secret",
        "bjt\x00 AND drop",
    ],
)
def test_hostile_queries_cannot_corrupt_or_crash_the_index(
    indexed: SearchDatabase, hostile: str
):
    from universal_search.query import QueryError

    try:
        SearchEngine(indexed).search(hostile)
    except QueryError:
        pass  # rejected is a valid, safe outcome
    with indexed.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 2
    assert [r.name for r in SearchEngine(indexed).search("bjt")]


def test_paths_with_traversal_and_sql_characters_round_trip(tmp_path: Path):
    tree = tmp_path / "tree"
    # Windows forbids '"' in file names, so the hostile characters that
    # can actually reach the index are the ones used here.
    weird = tree / "a'b; --.md"
    weird.parent.mkdir(parents=True)
    weird.write_text("contenido con bjt", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    results = SearchEngine(database).search("bjt")

    assert [r.name for r in results] == [weird.name]
    assert results[0].path == weird


def test_analysis_never_crashes_on_hostile_content(tmp_path: Path):
    hostile = [
        None, "", "   ", "\x00" * 100, "bjt" * 50_000,
        "'; DROP TABLE documents; --", "\udcff\udcfe garbage", "日本語 " * 500,
    ]
    for text in hostile:
        analysis = analyze(text, name="raro.md")
        assert analysis.version >= 1
        assert len(analysis.terms) <= 24


def test_oversized_documents_are_capped_not_unbounded(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    big = tree / "enorme.txt"
    big.write_text("bjt " * (MAX_CONTENT_CHARS // 2), encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    with database.connect() as connection:
        stored = connection.execute(
            "SELECT content FROM documents_fts"
        ).fetchone()[0]
    assert len(stored) <= MAX_CONTENT_CHARS
    # The index still works after a pathological document.
    assert SearchEngine(database).search("bjt")


def test_corrupt_database_is_reported_not_crashed(indexed: SearchDatabase):
    from universal_search.diagnostics import check, collect

    indexed.path.write_bytes(b"not a database")
    assert collect(indexed).error is not None
    assert check(indexed).status == "fatal"


def test_inaccessible_files_are_reported_and_do_not_stop_the_pass(tmp_path: Path):
    """One unreadable file must not cost the whole pass.

    Uses the indexer's own ``read_content`` seam instead of touching
    ``os.stat``: patching a stdlib module globally would break the test
    runner itself.
    """
    from universal_search.providers.local import read_local_content

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "legible.md").write_text("contenido bjt", encoding="utf-8")
    (tree / "bloqueado.md").write_text("secreto", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    def flaky(path: Path):
        if path.name == "bloqueado.md":
            raise OSError("access denied")
        return read_local_content(path)

    stats = Indexer(database).index_root(tree, read_content=flaky)

    # An unreadable file is still listed by name and path (like a binary),
    # but without text, and the failure is counted instead of swallowed.
    assert stats.created == 2
    assert stats.extraction_errors == 1
    by_name = {r.name for r in SearchEngine(database).search("bloqueado")}
    assert by_name == {"bloqueado.md"}
    # Its text was never extracted, so searching for that text finds
    # nothing — the name still does, as shown above.
    assert SearchEngine(database).search("secreto") == []


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symlinks unavailable on this platform"
)
def test_symlinked_directories_are_not_followed(tmp_path: Path):
    real = tmp_path / "real"
    (real).mkdir()
    (real / "secreto.md").write_text("contenido secreto bjt", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "normal.md").write_text("contenido normal bjt", encoding="utf-8")
    try:
        os.symlink(real, tree / "enlazado", target_is_directory=True)
    except OSError:  # pragma: no cover - no privileges on this machine
        pytest.skip("symlink creation not permitted")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)

    names = [r.name for r in SearchEngine(database).search("bjt")]
    assert "normal.md" in names
    assert "secreto.md" not in names  # the symlinked directory was not walked


# -- logging -------------------------------------------------------------------

def test_default_logs_hold_no_document_text_and_no_query(tmp_path: Path):
    secret = "FRASE-SECRETA-DEL-DOCUMENTO-5521"
    paths = AppPaths.discover(home=tmp_path / "home")
    logger = setup_logging(paths)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "secreto.txt").write_text(f"contenido {secret}", encoding="utf-8")
    (tree / "roto.pdf").write_bytes(f"%PDF-1.7 {secret}".encode("ascii"))
    try:
        Indexer(SearchDatabase(tmp_path / "index.db")).index_root(tree)
        SearchEngine(SearchDatabase(tmp_path / "index.db")).search("consulta privada")
        for handler in logger.handlers:
            handler.flush()
        text = paths.log_file.read_text(encoding="utf-8") if paths.log_file.exists() else ""
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    assert secret not in text
    assert "consulta privada" not in text
    assert logging.getLogger("universal_search").handlers == []


# -- privacy controls ----------------------------------------------------------

def test_optional_learning_is_off_by_default(tmp_path: Path):
    from universal_search.appconfig import AppConfig

    config = AppConfig.load(AppPaths.discover(home=tmp_path / "home"))
    assert config.usage_tracking is False


def test_cli_reports_the_inventory(indexed, tmp_path, monkeypatch, capsys):
    import unittest.mock as mock

    from universal_search.cli import main

    argv = ["universal-search", "privacy", "show",
            "--database", str(tmp_path / "index.db")]
    with mock.patch.object(sys, "argv", argv):
        main()
    out = capsys.readouterr().out
    assert "nothing leaves this machine" in out
    assert "intelligence" in out and "usage" in out
    assert "deletion:" in out


def test_cli_forget_reports_what_it_removed(indexed, tmp_path, monkeypatch, capsys):
    import unittest.mock as mock

    from universal_search.cli import main

    target = tmp_path / "tree" / "privado" / "secreto.txt"
    argv = ["universal-search", "privacy", "forget", str(target),
            "--database", str(tmp_path / "index.db")]
    with mock.patch.object(sys, "argv", argv):
        main()
    out = capsys.readouterr().out
    assert "Forgotten" in out
    assert "The file itself was not touched" in out
    assert not SearchEngine(indexed).search("privada")
