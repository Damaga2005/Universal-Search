"""Phase 028: safe local recovery cases."""

import pytest

from universal_search.appconfig import AppPaths
from universal_search.index.database import SearchDatabase
from universal_search.recovery import RecoveryConfirmationRequired, recover


def make_database(tmp_path) -> tuple[AppPaths, SearchDatabase]:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    return paths, SearchDatabase(paths.database)


def test_recover_removes_orphan_derived_rows_without_touching_sources(tmp_path) -> None:
    paths, database = make_database(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("contenido original", encoding="utf-8")
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO documents(id, source, path, name, extension, size)"
            " VALUES ('live', 'local', ?, 'source.md', '.md', 17)",
            (str(source),),
        )
        connection.execute(
            "INSERT INTO document_semantic(document_id, version,"
            " preprocessing_version, norm, words, content_hash)"
            " VALUES ('orphan', 1, 1, 1.0, '[]', NULL)"
        )
        connection.execute(
            "INSERT INTO document_semantic_terms(ngram, document_id, weight, idf,"
            " version, preprocessing_version) VALUES ('abc', 'orphan', 1.0, 1.0, 1, 1)"
        )
        connection.execute(
            "INSERT INTO document_graph_nodes(document_id, version,"
            " preprocessing_version, generation, name, path, source)"
            " VALUES ('orphan-graph', 1, 1, 'g', 'n.md', 'p.md', 'local')"
        )
        connection.commit()

    result = recover("orphan-derived", paths=paths)

    assert result.changed is True
    assert result.code == "repaired"
    assert source.read_text(encoding="utf-8") == "contenido original"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_semantic"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_semantic_terms"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_graph_nodes"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = 'live'"
        ).fetchone()[0] == 1


def test_recover_marks_dirty_derived_state(tmp_path) -> None:
    paths, database = make_database(tmp_path)

    result = recover("dirty-derived", paths=paths)

    assert result.code == "marked-dirty"
    with database.connect() as connection:
        metadata = dict(connection.execute(
            "SELECT key, value FROM document_semantic_metadata"
        ).fetchall())
        graph = dict(connection.execute(
            "SELECT key, value FROM document_graph_metadata"
        ).fetchall())
    assert metadata["dirty"] == "1"
    assert graph["dirty-all"] == "1"


def test_stale_coordination_is_removed_only_when_owner_is_dead(
    tmp_path, monkeypatch
) -> None:
    paths, _database = make_database(tmp_path)
    paths.lock_file.write_text("499999", encoding="ascii")
    (paths.home / "indexer.lock.owner").write_text(
        '{"pid": 499999, "generation": "dead"}', encoding="utf-8"
    )
    (paths.home / "indexer.starting.123.token.claim").write_text("x", encoding="ascii")
    monkeypatch.setattr("universal_search.background.process_alive", lambda pid: False)

    result = recover("stale-coordination", paths=paths)

    assert result.code == "repaired"
    assert not paths.lock_file.exists()
    assert not paths.worker_owner_file.exists()
    assert not (paths.home / "indexer.starting.123.token.claim").exists()


def test_live_owner_is_never_removed(tmp_path, monkeypatch) -> None:
    paths, _database = make_database(tmp_path)
    paths.lock_file.write_text("4242", encoding="ascii")
    monkeypatch.setattr("universal_search.background.process_alive", lambda pid: True)

    result = recover("stale-coordination", paths=paths)

    assert result.code == "owner-alive"
    assert result.changed is False
    assert paths.lock_file.exists()


def test_destructive_recovery_requires_confirmation(tmp_path) -> None:
    paths, database = make_database(tmp_path)
    with pytest.raises(RecoveryConfirmationRequired):
        recover("reset-derived", paths=paths, confirm=False)

    result = recover("reset-derived", paths=paths, confirm=True)

    assert result.code == "repaired"
    # Documents and their source files are never touched by derived resets.
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_recovery_never_accepts_unknown_case(tmp_path) -> None:
    paths, _database = make_database(tmp_path)
    with pytest.raises(ValueError):
        recover("delete-everything", paths=paths)
