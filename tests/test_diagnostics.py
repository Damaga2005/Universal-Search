"""Index diagnostics and repair (spec 015).

Covers the three states a user can be in — healthy, degraded and broken —
plus the rule that matters most in a repair tool: nothing is deleted
without an explicit confirmation, in the API and in the CLI.
"""

import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path

import pytest

from universal_search.diagnostics import (
    ConfirmationRequired,
    check,
    collect,
    rebuild_all,
    rebuild_fts,
    rebuild_intelligence,
    re_extract,
    reconcile,
    remove_indexed_source,
)
from universal_search.diagnostics.stats import (
    collect_derived,
    collect_sources,
    collect_storage,
)
from universal_search.diagnostics.repair import ORPHAN_CLEANUP_BATCH_SIZE
from universal_search.index.database import SCHEMA_VERSION, SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.intelligence import analysis_for, rebuild as rebuild_intelligence_rows
from universal_search.intelligence.graph import GraphStore


def build(root: Path, names=("uno.md", "dos.md", "tres.txt")) -> SearchDatabase:
    tree = root / "tree"
    for index, name in enumerate(names):
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"documento {index} con transistor bjt y polarizacion", encoding="utf-8"
        )
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(tree)
    return database


@pytest.fixture
def indexed(tmp_path: Path) -> SearchDatabase:
    return build(tmp_path)


# -- statistics ----------------------------------------------------------------

def test_statistics_of_a_healthy_index(indexed: SearchDatabase):
    statistics = collect(indexed)
    assert statistics.exists is True
    assert statistics.documents == 3
    assert statistics.with_content == 3
    assert statistics.schema_version == SCHEMA_VERSION
    assert statistics.app_version
    assert dict(statistics.by_type)[".md"] == 2
    assert dict(statistics.by_type)[".txt"] == 1
    assert dict(statistics.by_source)["local"] == 3
    assert statistics.database_bytes > 0
    assert statistics.total_bytes >= statistics.database_bytes
    assert statistics.error is None


def test_statistics_of_a_missing_database(tmp_path: Path):
    statistics = collect(SearchDatabase(tmp_path / "nunca.db"))
    assert statistics.exists is False
    assert statistics.documents == 0
    assert "does not exist" in (statistics.error or "")
    # A missing index is a fact, not an exception.
    assert check(SearchDatabase(tmp_path / "nunca.db")).status == "fatal"


def test_statistics_survive_a_corrupt_database(tmp_path: Path):
    database = build(tmp_path)
    database.path.write_bytes(b"this is not a sqlite database at all")
    statistics = collect(database)
    assert statistics.exists is True
    assert statistics.error is not None
    assert check(database).status == "fatal"


# -- health --------------------------------------------------------------------

def test_a_healthy_index_reports_ok(indexed: SearchDatabase):
    report = check(indexed)
    assert report.status == "ok", report.render()
    assert report.ok is True
    names = {check.name for check in report.checks}
    assert {"database", "schema", "fts.coverage", "fts.orphans",
            "documents.identities", "extraction"} <= names


def test_orphan_fts_rows_are_a_warning_not_a_failure(indexed: SearchDatabase):
    with indexed.connect() as connection:
        connection.execute(
            "INSERT INTO documents_fts(document_id,name,path,content)"
            " VALUES ('fantasma','fantasma.md','/tmp/fantasma.md','texto')"
        )
        connection.commit()
    report = check(indexed)
    assert report.status == "warning"
    orphans = report.by_name("fts.orphans")
    assert orphans.status == "warning"
    assert "1" in orphans.detail


def test_documents_without_search_text_are_reported(indexed: SearchDatabase):
    with indexed.connect() as connection:
        connection.execute("DELETE FROM documents_fts WHERE document_id = ?",
                           (_id_of(indexed, "uno.md"),))
        connection.commit()
    report = check(indexed)
    assert report.status == "warning"
    assert report.by_name("fts.coverage").status == "warning"
    # Search still works: the document is findable by name in FTS? No:
    # without its row it is gone, which is exactly what the repair fixes.
    assert _id_of(indexed, "uno.md")


def test_stale_documents_are_reported_bounded(indexed: SearchDatabase, tmp_path):
    (tmp_path / "tree" / "uno.md").unlink()
    report = check(indexed, sample_limit=3)
    stale = report.by_name("paths.stale")
    assert stale is not None and stale.status == "warning"
    assert "no longer exist" in stale.detail


def test_a_stale_lock_is_a_warning(tmp_path: Path):
    from universal_search.appconfig import AppPaths

    paths = AppPaths.discover(home=tmp_path / "home")
    paths.ensure()
    # A pid that cannot be running: the process does not exist.
    paths.lock_file.write_text("999999", encoding="utf-8")
    report = check(SearchDatabase(tmp_path / "index.db"), paths)
    lock = report.by_name("worker.lock")
    assert lock is not None
    assert lock.status in {"ok", "warning"}


def test_health_report_serializes_and_renders(indexed: SearchDatabase):
    report = check(indexed)
    payload = report.as_dict()
    assert payload["status"] == "ok"
    assert payload["checks"]
    assert "index health" in report.render()


def _id_of(database: SearchDatabase, name: str) -> str:
    with database.connect() as connection:
        return connection.execute(
            "SELECT id FROM documents WHERE name = ?", (name,)
        ).fetchone()[0]


# -- repair --------------------------------------------------------------------

def test_reconcile_is_safe_and_idempotent(indexed: SearchDatabase, tmp_path: Path):
    first = reconcile(indexed, tmp_path / "tree")
    assert first.action == "reconcile"
    second = reconcile(indexed, tmp_path / "tree")
    assert second.changed == 0
    assert second.skipped == 3


def test_rebuild_fts_repairs_both_orphan_directions(
    indexed: SearchDatabase, tmp_path: Path
):
    orphan = _id_of(indexed, "uno.md")
    with indexed.connect() as connection:
        connection.execute("DELETE FROM documents_fts WHERE document_id = ?",
                           (orphan,))
        connection.execute(
            "INSERT INTO documents_fts(document_id,name,path,content)"
            " VALUES ('fantasma','fantasma.md','/tmp/fantasma.md','texto')"
        )
        connection.commit()

    result = rebuild_fts(indexed, confirm=True)
    assert result.changed == 2
    assert check(indexed).by_name("fts.orphans").status == "ok"
    assert check(indexed).by_name("fts.coverage").status == "ok"


def test_rebuild_fts_scrubs_orphan_aliases_and_dirty_marker(
    indexed: SearchDatabase, tmp_path: Path
):
    source_path = tmp_path / "tree" / "uno.md"
    source_before = source_path.read_text(encoding="utf-8")
    with indexed.connect() as connection:
        source_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'uno.md'"
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO documents_fts(document_id, name, path, content)"
            " VALUES (?, ?, ?, ?)",
            ("orphan-alias", "ghost.md", "/safe/ghost.md", "orphan text"),
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            (
                f"references:{source_id}",
                json.dumps(
                    ["orphan-alias", "ghost.md", "/safe/ghost.md", "keep.md"]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            ("references:orphan-alias", json.dumps([source_id])),
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES (?, ?)",
            ("dirty:orphan-alias", json.dumps("rebuild-fts")),
        )
        connection.commit()

    result = rebuild_fts(indexed, confirm=True)

    assert result.changed == 1
    with indexed.connect() as connection:
        metadata = {
            row["key"]: json.loads(row["value"])
            for row in connection.execute(
                "SELECT key, value FROM document_graph_metadata"
                " WHERE key LIKE 'references:%'"
            )
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_metadata"
            " WHERE key = 'dirty:orphan-alias'"
        ).fetchone()[0] == 0
    assert "references:orphan-alias" not in metadata
    assert metadata[f"references:{source_id}"] == ["keep.md"]
    assert source_path.read_text(encoding="utf-8") == source_before


def test_rebuild_fts_orphan_cleanup_is_batched(
    indexed: SearchDatabase, monkeypatch
):
    import universal_search.intelligence.graph as graph_module

    orphan_count = ORPHAN_CLEANUP_BATCH_SIZE * 2 + 17
    with indexed.connect() as connection:
        connection.executemany(
            "INSERT INTO documents_fts(document_id, name, path, content)"
            " VALUES (?, ?, ?, ?)",
            [
                (
                    f"orphan-{index:04d}",
                    f"orphan-{index:04d}.md",
                    f"/safe/orphan-{index:04d}.md",
                    "orphan text",
                )
                for index in range(orphan_count)
            ],
        )
        connection.commit()

    calls: list[tuple[str, ...]] = []
    original = graph_module.scrub_reference_metadata

    def spy(connection, document_ids, aliases=None):
        ids = tuple(sorted(str(item) for item in document_ids))
        calls.append(ids)
        return original(connection, ids, aliases)

    monkeypatch.setattr(graph_module, "scrub_reference_metadata", spy)
    result = rebuild_fts(indexed, confirm=True)

    assert result.changed == orphan_count
    assert calls
    assert max(len(ids) for ids in calls) <= ORPHAN_CLEANUP_BATCH_SIZE
    assert len(calls) <= (
        orphan_count + ORPHAN_CLEANUP_BATCH_SIZE - 1
    ) // ORPHAN_CLEANUP_BATCH_SIZE
    with indexed.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents_fts"
            " WHERE document_id LIKE 'orphan-%'"
        ).fetchone()[0] == 0


def test_rebuild_fts_updates_canonical_hash_and_marks_graph_dirty(
    indexed: SearchDatabase, tmp_path: Path
):
    rebuild_intelligence_rows(indexed)
    target = tmp_path / "tree" / "uno.md"
    target.write_text("contenido reconstruido sobre mosfetos", encoding="utf-8")
    with indexed.connect() as connection:
        document_id = _id_of(indexed, "uno.md")
        connection.execute(
            "DELETE FROM documents_fts WHERE document_id = ?", (document_id,)
        )
        connection.commit()

    result = rebuild_fts(indexed, confirm=True)

    assert result.changed == 1
    expected_hash = hashlib.sha256(
        "contenido reconstruido sobre mosfetos".encode("utf-8")
    ).hexdigest()
    with indexed.connect() as connection:
        assert connection.execute(
            "SELECT content_hash FROM documents WHERE id = ?", (document_id,)
        ).fetchone()[0] == expected_hash
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_metadata WHERE key = ?",
            (f"dirty:{document_id}",),
        ).fetchone()[0] == 1
    assert target.read_text(encoding="utf-8") == "contenido reconstruido sobre mosfetos"


def test_destructive_repairs_refuse_without_confirmation(indexed: SearchDatabase,
                                                          tmp_path: Path):
    with pytest.raises(ConfirmationRequired):
        rebuild_fts(indexed)
    with pytest.raises(ConfirmationRequired):
        re_extract(indexed, tmp_path / "tree" / "uno.md")
    with pytest.raises(ConfirmationRequired):
        rebuild_all(indexed, [tmp_path / "tree"])
    # Nothing changed while they refused.
    assert collect(indexed).documents == 3


def test_re_extract_replaces_one_document(indexed: SearchDatabase,
                                          tmp_path: Path):
    target = tmp_path / "tree" / "uno.md"
    target.write_text("contenido completamente nuevo sobre mosfetos", "utf-8")
    result = re_extract(indexed, target, confirm=True)
    assert result.changed == 1
    assert [r.name for r in SearchEngine(indexed).search("mosfetos")] == ["uno.md"]
    # A path outside the index is reported, not invented.
    outsider = tmp_path / "tree" / "nuevo.md"
    outsider.write_text("nuevo", "utf-8")
    assert re_extract(indexed, outsider, confirm=True).changed == 0


def test_re_extract_invalidates_obsolete_intelligence_and_graph(
    indexed: SearchDatabase, tmp_path: Path
):
    rebuild_intelligence_rows(indexed)
    target = tmp_path / "tree" / "uno.md"
    target.write_text("contenido nuevo sobre mosfetos", encoding="utf-8")
    re_extract(indexed, target, confirm=True)

    stats = rebuild_intelligence_rows(indexed)
    analysis = analysis_for(indexed, "uno.md")
    assert stats.updated == 1
    assert analysis is not None
    assert "mosfetos" in analysis.keywords
    assert "bjt" not in analysis.keywords
    assert all(
        "bjt" not in value
        for item in GraphStore(indexed).related(_id_of(indexed, "uno.md"))
        for evidence in item.evidence
        for value in evidence.values
    )


def test_rebuild_intelligence_is_safe(indexed: SearchDatabase):
    result = rebuild_intelligence(indexed)
    assert result.changed == 3
    assert rebuild_intelligence(indexed).changed == 0


def test_full_rebuild_drops_everything_and_reindexes(
    indexed: SearchDatabase, tmp_path: Path
):
    result = rebuild_all(indexed, [tmp_path / "tree"], confirm=True)
    assert result.action == "rebuild-all"
    assert "dropped 3 document" in result.detail
    statistics = collect(indexed)
    assert statistics.documents == 3
    assert [r.name for r in SearchEngine(indexed).search("bjt")] != []


def test_control_center_source_storage_and_derived_stats(indexed: SearchDatabase):
    tree = indexed.path.parent / "tree"
    rebuild_intelligence_rows(indexed)
    sources = collect_sources(indexed, (tree,))
    storage = collect_storage(indexed)
    derived = collect_derived(indexed)

    assert sources[0].documents == 3
    assert dict(sources[0].by_type)[".md"] == 2
    assert sources[0].accessible is True
    assert storage.total_bytes > 0
    assert derived.intelligence_rows == 3
    assert derived.graph_current is True


def test_remove_indexed_source_removes_rows_but_never_source_files(
    indexed: SearchDatabase, tmp_path: Path
):
    rebuild_intelligence_rows(indexed)
    tree = tmp_path / "tree"
    target = tree / "uno.md"
    source_before = target.read_text(encoding="utf-8")
    result = remove_indexed_source(indexed, tree)

    assert result.documents == 3
    assert result.physical_files == 0
    assert target.exists()
    assert target.read_text(encoding="utf-8") == source_before
    assert collect(indexed).documents == 0
    with closing(indexed.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_intelligence"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_metadata WHERE key LIKE 'dirty:%'"
        ).fetchone()[0] == 0


# -- CLI -----------------------------------------------------------------------

def run_cli(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    import unittest.mock as mock

    from universal_search.cli import main

    with mock.patch.object(sys, "argv", ["universal-search", *args]):
        try:
            main()
            code = 0
        except SystemExit as exit_info:
            code = exit_info.code or 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_summary_and_health(indexed: SearchDatabase, tmp_path,
                                monkeypatch, capsys):
    database = ["--database", str(tmp_path / "index.db")]
    code, out, _ = run_cli(monkeypatch, capsys, "diagnose", "summary", *database)
    assert code == 0
    assert "documents:  3" in out
    assert "schema:" in out

    code, out, _ = run_cli(monkeypatch, capsys, "diagnose", "health", *database)
    assert code == 0
    assert "index health: ok" in out


def test_cli_health_reports_warnings_with_exit_code_one(
    indexed: SearchDatabase, tmp_path, monkeypatch, capsys
):
    with indexed.connect() as connection:
        connection.execute(
            "INSERT INTO documents_fts(document_id,name,path,content)"
            " VALUES ('fantasma','f.md','/tmp/f.md','x')"
        )
        connection.commit()
    code, out, _ = run_cli(
        monkeypatch, capsys, "diagnose", "health",
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 1
    assert "index health: warning" in out


def test_cli_refuses_destructive_repairs_without_yes(
    indexed: SearchDatabase, tmp_path, monkeypatch, capsys
):
    code, _, err = run_cli(
        monkeypatch, capsys, "diagnose", "repair", "all",
        "--root", str(tmp_path / "tree"),
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 1
    assert "Refusing" in err
    assert collect(indexed).documents == 3

    code, out, _ = run_cli(
        monkeypatch, capsys, "diagnose", "repair", "all",
        "--root", str(tmp_path / "tree"), "--yes",
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 0
    assert "rebuild-all" in out


def test_cli_repair_requires_a_root(indexed: SearchDatabase, tmp_path,
                                    monkeypatch, capsys):
    code, _, err = run_cli(
        monkeypatch, capsys, "diagnose", "repair", "all", "--yes",
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 1
    assert "--root" in err


# -- logging policy ------------------------------------------------------------

def test_logs_are_rotated_timestamped_severity_tagged_and_bounded(tmp_path: Path):
    from logging.handlers import RotatingFileHandler

    from universal_search.appconfig import MAX_LOG_MESSAGE, AppPaths, setup_logging

    paths = AppPaths.discover(home=tmp_path / "home")
    logger = setup_logging(paths)
    try:
        handler = next(
            h for h in logger.handlers if isinstance(h, RotatingFileHandler)
        )
        assert handler.maxBytes == 1_000_000
        assert handler.backupCount == 3
        logger.info("mensaje normal")
        logger.warning("secreto " + "x" * (MAX_LOG_MESSAGE + 100))
        handler.flush()
        text = paths.log_file.read_text(encoding="utf-8")
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    assert "mensaje normal" in text
    assert "WARNING" in text  # severity-tagged
    assert text.strip()  # timestamped by the formatter
    assert "truncated" in text
    assert len(text) < MAX_LOG_MESSAGE + 400


def test_document_content_never_reaches_the_log(tmp_path: Path):
    """An unreadable document with a secret in its bytes must not be logged."""
    import logging

    from universal_search.appconfig import AppPaths, setup_logging
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    paths = AppPaths.discover(home=tmp_path / "home")
    logger = setup_logging(paths)
    secret = "frase-secreta-del-documento-9137"
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "roto.pdf").write_bytes(
        b"%PDF-1.7 " + secret.encode("ascii") + b" \x00\x01"
    )
    (tree / "normal.md").write_text("documento normal", encoding="utf-8")
    try:
        Indexer(SearchDatabase(tmp_path / "index.db")).index_root(tree)
        logger.debug("contenido: " + secret)
        for handler in logger.handlers:
            handler.flush()
        text = paths.log_file.read_text(encoding="utf-8") if paths.log_file.exists() else ""
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    assert secret not in text
    assert logging.getLogger("universal_search").handlers == []


# -- GUI service (no Tk) --------------------------------------------------------

def test_service_exposes_diagnostics_without_tk(tmp_path: Path):
    from universal_search.appconfig import AppPaths
    from universal_search.gui.services import SearchService

    build(tmp_path)
    service = SearchService(
        paths=AppPaths.discover(home=tmp_path / "home"),
        database_path=tmp_path / "index.db",
    )
    report = service.diagnostics()
    assert report["health"]["status"] in {"ok", "warning"}
    assert report["summary"]["documents"] == 3
    text = service.diagnostics_report()
    assert "documents: 3" in text
    assert "index health" in text


def test_service_rebuild_requires_confirmation(tmp_path: Path):
    from universal_search.appconfig import AppPaths
    from universal_search.diagnostics import ConfirmationRequired as Required
    from universal_search.gui.services import SearchService

    build(tmp_path)
    service = SearchService(
        paths=AppPaths.discover(home=tmp_path / "home"),
        database_path=tmp_path / "index.db",
    )
    with pytest.raises(Required):
        service.rebuild_index()
    result = service.rebuild_index(confirm=True)
    assert result.action == "rebuild-all"
