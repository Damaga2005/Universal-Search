"""Phase 023: indexing control-center service and safety boundaries."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.gui.control_center import (
    ActionResult,
    ControlCenterService,
    ControlSnapshot,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.intelligence import rebuild as rebuild_intelligence
from universal_search.platforms.worker import FileLease
from universal_search.providers.base import ENUMERATE, METADATA, ProviderInfo


def make_service(
    tmp_path: Path,
    *,
    roots: tuple[Path, ...] = (),
    create_roots: bool = True,
) -> tuple[ControlCenterService, AppPaths, SearchDatabase, tuple[Path, ...]]:
    source_roots = roots or (tmp_path / "source",)
    for root in source_roots:
        if create_roots:
            root.mkdir(parents=True, exist_ok=True)
            (root / "notes.md").write_text("BJT Ebers-Moll notes", encoding="utf-8")
            (root / "readme.txt").write_text("CMOS MUX overview", encoding="utf-8")

    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(roots=tuple(str(root) for root in source_roots)).save(paths)
    database = SearchDatabase(tmp_path / "index.db")
    for root in source_roots:
        if root.is_dir():
            Indexer(database).index_root(root)
    service = ControlCenterService(paths=paths, database_path=database.path)
    return service, paths, database, source_roots


def test_snapshot_exposes_sources_counts_types_storage_and_derived_state(
    tmp_path: Path,
) -> None:
    service, _paths, database, (root,) = make_service(tmp_path)
    rebuild_intelligence(database)

    snapshot = service.snapshot()

    assert isinstance(snapshot, ControlSnapshot)
    assert snapshot.health["status"] in {"ok", "warning"}
    assert snapshot.health_status in {"ok", "warning"}
    assert len(snapshot.sources) == 1
    source = snapshot.sources[0]
    assert source.path == str(root)
    assert source.document_count == 2
    assert source.count == 2
    assert source.available is True
    assert source.accessible is True
    assert dict(source.supported_types)[".md"] == 1
    assert dict(source.supported_types)[".txt"] == 1
    assert source.indexed_bytes > 0
    assert snapshot.derived.intelligence_rows == 2
    assert snapshot.storage.total_bytes > 0
    assert snapshot.storage.database_bytes > 0
    assert snapshot.worker.state in {"stopped", "starting", "idle", "indexing"}


def test_add_source_is_deduplicated_and_ordinary_remove_keeps_files_and_rows(
    tmp_path: Path,
) -> None:
    service, paths, database, _ = make_service(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "extra.md").write_text("extra content", encoding="utf-8")

    added = service.add_source(extra)
    duplicate = service.add_source(extra)
    assert isinstance(added, ActionResult)
    assert added.ok is True
    assert duplicate.ok is True
    assert duplicate.changed == 0
    assert str(extra) in AppConfig.load(paths).roots

    Indexer(database).index_root(extra)
    before = _document_count(database)
    removed = service.remove_source(extra)
    after = _document_count(database)

    assert removed.ok is True
    assert removed.indexed_records == 0
    assert removed.physical_files == 0
    assert after == before
    assert (extra / "extra.md").exists()
    assert str(extra) not in AppConfig.load(paths).roots


def test_remove_source_with_delete_indexed_removes_only_index_and_derived_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, paths, database, _ = make_service(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    target = extra / "extra.md"
    target.write_text("derived content", encoding="utf-8")
    service.add_source(extra)
    Indexer(database).index_root(extra)
    rebuild_intelligence(database)

    def refuse_source_unlink(self: Path, *args, **kwargs):
        if self == target:
            raise AssertionError("source files must never be unlinked")
        return original_unlink(self, *args, **kwargs)

    original_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", refuse_source_unlink)
    result = service.remove_source(extra, delete_indexed=True)

    assert result.ok is True
    assert result.indexed_records == 1
    assert result.derived_records >= 1
    assert result.physical_files == 0
    assert result.source_files_deleted == 0
    assert target.exists()
    assert str(extra) not in AppConfig.load(paths).roots
    assert _document_count(database) == 2
    with closing(database.connect()) as connection:
        target_id = connection.execute(
            "SELECT id FROM documents WHERE path = ?", (str(target),)
        ).fetchone()
        assert target_id is None
        assert connection.execute(
            "SELECT COUNT(*) FROM document_intelligence"
        ).fetchone()[0] == 2


def test_rescan_reports_changes_and_tracks_source_state(tmp_path: Path) -> None:
    service, _paths, database, (root,) = make_service(tmp_path)
    (root / "new.md").write_text("newly discovered", encoding="utf-8")

    result = service.rescan(root)

    assert result.ok is True
    assert result.action == "rescan"
    assert result.indexed_records == 1
    assert result.physical_files == 0
    snapshot = service.snapshot()
    assert snapshot.sources[0].document_count == 3
    assert snapshot.sources[0].last_scan_at is not None
    assert snapshot.last_scan_at is not None
    assert _document_count(database) == 3


def test_rescan_all_and_retry_failures_report_inaccessible_roots(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    service, _paths, _database, _ = make_service(
        tmp_path, roots=(missing,), create_roots=False
    )

    result = service.rescan()
    retry = service.retry_failures()
    snapshot = service.snapshot()

    assert result.ok is False
    assert result.errors
    assert retry.ok is False
    assert snapshot.sources[0].accessible is False
    assert snapshot.sources[0].failures
    assert snapshot.failures


def test_a_temporarily_disconnected_source_can_be_added_and_reported(
    tmp_path: Path,
) -> None:
    service, _paths, _database, _ = make_service(tmp_path)
    disconnected = tmp_path / "network-share-not-mounted"

    result = service.add_source(disconnected)
    snapshot = service.snapshot()

    assert result.ok is True
    assert result.metadata["accessible"] is False
    assert snapshot.sources[-1].accessible is False
    assert snapshot.sources[-1].status == "inaccessible"


def test_pause_and_resume_return_typed_results(tmp_path: Path, monkeypatch) -> None:
    service, _paths, _database, _ = make_service(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        service.background,
        "pause",
        lambda: calls.append("pause") or ("paused", "en pausa"),
    )
    monkeypatch.setattr(
        service.background,
        "resume",
        lambda: calls.append("resume") or ("resumed", "reanudada"),
    )

    paused = service.pause()
    resumed = service.resume()

    assert paused.ok is True and paused.action == "pause"
    assert resumed.ok is True and resumed.action == "resume"
    assert calls == ["pause", "resume"]


def test_snapshot_surfaces_stale_worker_without_claiming_a_live_worker(
    tmp_path: Path, monkeypatch
) -> None:
    service, paths, _database, _ = make_service(tmp_path)
    paths.lock_file.write_text("999999", encoding="ascii")
    from universal_search import background

    monkeypatch.setattr(background, "process_alive", lambda _pid: False)
    monkeypatch.setattr(background, "get_autostart", lambda **_kwargs: False)

    snapshot = service.snapshot()

    assert snapshot.worker.state == "stopped"
    assert snapshot.worker.stale_lock is True
    assert snapshot.stale_worker is True
    assert snapshot.health["status"] in {"ok", "warning", "fatal"}


def test_corrupt_database_is_a_health_fact_not_an_untyped_exception(
    tmp_path: Path,
) -> None:
    service, _paths, database, _ = make_service(tmp_path)
    database.path.write_bytes(b"not sqlite")

    snapshot = service.snapshot()
    result = service.rescan()

    assert snapshot.health["status"] == "fatal"
    assert result.ok is False
    assert result.message


def test_rebuild_kinds_have_explicit_scope_and_full_rebuild_confirmation(
    tmp_path: Path,
) -> None:
    service, _paths, database, (root,) = make_service(tmp_path)
    rebuild_intelligence(database)

    refused_fts = service.rebuild("fts")
    refused_full = service.rebuild("full")
    assert refused_fts.ok is False
    assert refused_fts.requires_confirmation is True
    assert refused_fts.changed == 0
    assert refused_full.ok is False
    assert refused_full.requires_confirmation is True
    assert refused_full.data_scope == "indexed_records"
    assert _document_count(database) == 2

    rebuilt_relationships = service.rebuild("relationships", confirm=True)
    rebuilt_derived = service.rebuild("intelligence", confirm=True)
    rebuilt_full = service.rebuild("all", confirm=True)

    assert rebuilt_relationships.ok is True
    assert rebuilt_relationships.data_scope == "derived_data"
    assert rebuilt_derived.ok is True
    assert rebuilt_full.ok is True
    assert rebuilt_full.indexed_records >= 0
    assert rebuilt_full.physical_files == 0
    assert root.exists()


def test_concurrent_mutating_actions_fail_closed_as_busy(tmp_path: Path) -> None:
    service, _paths, _database, (root,) = make_service(tmp_path)
    service._action_lock.acquire()
    try:
        result = service.rescan(root)
    finally:
        service._action_lock.release()

    assert isinstance(result, ActionResult)
    assert result.ok is False
    assert result.code == "busy"
    assert result.errors


def test_control_state_is_persisted_as_operational_metadata(tmp_path: Path) -> None:
    service, paths, _database, (root,) = make_service(tmp_path)
    service.rescan(root)

    assert paths.control_state_file.exists()
    payload = json.loads(paths.control_state_file.read_text(encoding="utf-8"))
    assert payload["last_scan_at"]
    assert payload["last_scan_stats"]


def test_nested_roots_have_single_document_ownership_and_safe_parent_removal(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "parent.md").write_text("parent document", encoding="utf-8")
    (child / "child.md").write_text("child document", encoding="utf-8")
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(roots=(str(parent), str(child))).save(paths)
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(parent)
    service = ControlCenterService(paths=paths, database_path=database.path)

    snapshot = service.snapshot()
    assert [source.document_count for source in snapshot.sources] == [1, 1]
    assert snapshot.document_count == 2

    # A whole-tree rescan reports the nested child once, not once per
    # configured parent/child pair.
    whole = service.rescan()
    assert whole.ok is True
    assert whole.metadata["stats"]["unchanged"] == 2

    result = service.remove_source(parent, delete_indexed=True)

    assert result.ok is True
    config = AppConfig.load(paths)
    assert str(child) in config.roots
    assert str(parent) not in config.roots
    assert (parent / "parent.md").exists()
    assert (child / "child.md").exists()
    with closing(database.connect()) as connection:
        paths_left = {
            str(row["path"]) for row in connection.execute("SELECT path FROM documents")
        }
    assert paths_left == {str(child / "child.md")}


def test_config_persistence_failure_rolls_back_indexed_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, paths, database, (root,) = make_service(tmp_path)
    original_save = AppConfig.save
    calls = 0

    def fail_first_save(config, supplied_paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected config failure")
        return original_save(config, supplied_paths)

    monkeypatch.setattr(AppConfig, "save", fail_first_save)
    result = service.remove_source(root, delete_indexed=True)

    assert result.ok is False
    assert result.code == "config-error"
    assert str(root) in AppConfig.load(paths).roots
    assert _document_count(database) == 2
    assert (root / "notes.md").exists()


def test_indexed_database_failure_restores_source_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import universal_search.gui.control_center as control_center_module

    service, paths, database, (root,) = make_service(tmp_path)

    def fail_database(_database, _root, **_kwargs):
        raise RuntimeError("injected database failure")

    monkeypatch.setattr(
        control_center_module, "remove_indexed_source", fail_database
    )
    result = service.remove_source(root, delete_indexed=True)

    assert result.ok is False
    assert result.code == "database-error"
    assert str(root) in AppConfig.load(paths).roots
    assert _document_count(database) == 2


def test_postcommit_index_failure_keeps_config_and_index_in_one_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import universal_search.gui.control_center as control_center_module

    service, paths, database, (root,) = make_service(tmp_path)
    original_remove = control_center_module.remove_indexed_source

    def delete_then_report_failure(*args, **kwargs):
        original_remove(*args, **kwargs)
        raise RuntimeError("injected post-commit report failure")

    monkeypatch.setattr(
        control_center_module, "remove_indexed_source", delete_then_report_failure
    )
    result = service.remove_source(root, delete_indexed=True)

    config = AppConfig.load(paths)
    rows = _document_count(database)
    assert (str(root) not in config.roots) == (rows == 0)
    assert result.ok is True or result.code == "recovery-required"
    assert (root / "notes.md").exists()


def test_worker_lease_blocks_source_mutation_across_service_instances(
    tmp_path: Path,
) -> None:
    service, paths, database, (root,) = make_service(tmp_path)
    other = ControlCenterService(paths=paths, database_path=database.path)
    lease = FileLease(paths.worker_lease_file)
    assert lease.acquire() is True
    try:
        result = other.remove_source(root, delete_indexed=True)
    finally:
        lease.release()

    assert result.ok is False
    assert result.code == "worker-busy"
    assert str(root) in AppConfig.load(paths).roots
    assert _document_count(database) == 2


def test_live_worker_status_blocks_source_mutation_even_without_a_test_lease(
    tmp_path: Path,
) -> None:
    from universal_search.background_service import BackgroundStatus

    class BusyBackground:
        def status(self):
            return BackgroundStatus(state="indexing", pid=4242)

        def pause(self):
            return "paused", "paused"

        def resume(self):
            return "resumed", "resumed"

    service, paths, database, (root,) = make_service(tmp_path)
    service.background = BusyBackground()
    result = service.remove_source(root, delete_indexed=True)

    assert result.ok is False
    assert result.code == "worker-busy"
    assert str(root) in AppConfig.load(paths).roots
    assert _document_count(database) == 2


def test_unavailable_provider_is_not_rendered_healthy_or_scanned(
    tmp_path: Path,
) -> None:
    class UnavailableRegistry:
        def infos(self):
            return (
                ProviderInfo(
                    key="local",
                    kind="filesystem",
                    version="test",
                    interface_version=1,
                    capabilities=(ENUMERATE, METADATA),
                    available=False,
                    detail="test provider unavailable",
                ),
            )

    service, _paths, _database, (root,) = make_service(tmp_path)
    service.registry = UnavailableRegistry()
    calls: list[object] = []

    def forbidden_indexer(_database):
        calls.append(_database)
        raise AssertionError("unavailable provider must not be scanned")

    service.indexer_factory = forbidden_indexer
    snapshot = service.snapshot()
    result = service.rescan(root)

    source = snapshot.sources[0]
    assert source.available is False
    assert source.status == "unavailable"
    assert result.ok is False
    assert result.code == "provider-unavailable"
    assert calls == []


def test_malformed_state_and_stale_failures_are_tolerated_and_reconciled(
    tmp_path: Path,
) -> None:
    service, paths, _database, (root,) = make_service(tmp_path)
    removed = tmp_path / "removed-root"
    paths.control_state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "failures": [
                    {"path": str(removed), "message": "old", "count": "not-an-int"},
                    {"path": str(root), "message": "retry me", "count": 1},
                ],
            }
        ),
        encoding="utf-8",
    )

    fresh = ControlCenterService(paths=paths, database_path=service.database.path)
    result = fresh.retry_failures()

    assert result.ok is True
    assert str(root) in result.metadata.get("roots", [])
    assert str(removed) not in result.metadata.get("roots", [])
    assert not any(failure.path == str(removed) for failure in fresh.failures)


def test_full_rebuild_recovers_corrupt_database_and_reports_owned_files(
    tmp_path: Path,
) -> None:
    service, _paths, database, (root,) = make_service(tmp_path)
    source = root / "notes.md"
    source_before = source.read_bytes()
    database.path.write_bytes(b"corrupt application database")

    result = service.rebuild("all", confirm=True)

    assert result.ok is True
    assert result.source_files_deleted == 0
    assert result.application_files >= 1
    assert database.path.exists()
    assert source.exists()
    assert source.read_bytes() == source_before
    assert _document_count(database) == 2


def test_confirmation_provenance_is_explicit(tmp_path: Path) -> None:
    service, _paths, _database, _ = make_service(tmp_path)
    refused = service.rebuild("full")
    accepted = service.rebuild("full", confirm=True)

    assert refused.confirmation_provenance == "required"
    assert refused.confirmed is False
    assert accepted.confirmation_provenance == "caller"
    assert accepted.confirmed is True


def test_storage_reports_application_owned_database_files_separately(
    tmp_path: Path,
) -> None:
    service, _paths, _database, _ = make_service(tmp_path)
    snapshot = service.snapshot()
    assert snapshot.storage.application_files >= 1
    assert snapshot.storage.source_files == 0


def _document_count(database: SearchDatabase) -> int:
    with closing(database.connect()) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        )
