"""Release reliability: migrations, downgrade refusal, backups, recovery (020).

The phase-010 suite covers versioning and install/uninstall. This file
covers what a *released* build has to survive afterwards: an index written
by a newer build, an upgrade from a representative older index, a backup
before a destructive repair, and a worker that died mid-pass.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from universal_search import __version__
from universal_search.diagnostics import check, collect, rebuild_all
from universal_search.index.database import (
    SCHEMA_VERSION,
    SearchDatabase,
    UnsupportedSchemaVersion,
)
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine

# The schema of the phase-010 release: no intelligence, no migration ledger.
PHASE_010_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT,
    modified_at TEXT,
    content_hash TEXT,
    mtime_ns INTEGER,
    last_seen_run INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'available',
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS usage_events_document
    ON usage_events(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    document_id UNINDEXED,
    name,
    path,
    content,
    tokenize = 'unicode61'
);
"""


def build(root: Path, count: int = 3) -> SearchDatabase:
    tree = root / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (tree / f"doc-{index}.md").write_text(
            f"documento {index} sobre transistores bjt", encoding="utf-8"
        )
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(tree)
    return database


def phase_010_index(root: Path) -> SearchDatabase:
    """A database exactly as the 1.0.0 release left it (schema 3)."""
    import sqlite3

    path = root / "legacy.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PHASE_010_SCHEMA)
        connection.execute(
            "INSERT INTO documents(id,source,path,name,extension,size,content_hash)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                "doc-antiguo",
                "local",
                str(root / "tree" / "antiguo.md"),
                "antiguo.md",
                ".md",
                10,
                "hash",
            ),
        )
        connection.execute(
            "INSERT INTO documents_fts(document_id,name,path,content)"
            " VALUES (?,?,?,?)",
            (
                "doc-antiguo",
                "antiguo.md",
                str(root / "tree" / "antiguo.md"),
                "contenido antiguo con bjt",
            ),
        )
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    finally:
        connection.close()
    return SearchDatabase(path)


# -- upgrade ------------------------------------------------------------------

def test_upgrade_from_the_release_schema_keeps_the_index(tmp_path: Path):
    database = phase_010_index(tmp_path)

    # Opening it applies the pending migrations and stamps the new version.
    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master")
        }
    assert version == SCHEMA_VERSION
    assert {"document_intelligence", "schema_migrations"} <= objects

    # The pre-existing document and its searchable text are untouched.
    results = SearchEngine(database).search("antiguo")
    assert [r.name for r in results] == ["antiguo.md"]
    assert results[0].snippet and "antiguo" in results[0].snippet


def test_migration_history_records_each_applied_version(tmp_path: Path):
    database = phase_010_index(tmp_path)
    with database.connect():
        pass

    history = database.migration_history()
    assert [version for version, _ in history] == [SCHEMA_VERSION]
    assert history[0][1]  # an ISO timestamp

    # Reconnecting must not append a second row for the same version.
    with database.connect():
        pass
    with database.connect():
        pass
    assert len(database.migration_history()) == 1


def test_history_survives_a_second_distinct_upgrade(tmp_path: Path):
    """A ledger with several versions reads oldest first, one row each."""
    database = build(tmp_path)
    with database.connect() as connection:
        # Simulate an older build's stamp plus its ledger row, as an
        # upgrade from an intermediate release would have left it.
        connection.execute("DELETE FROM schema_migrations")
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (4)"
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    with database.connect():
        pass

    history = [version for version, _ in database.migration_history()]
    assert history == [4, SCHEMA_VERSION]


# -- downgrade refusal --------------------------------------------------------

def test_a_newer_index_is_refused_not_downgraded(tmp_path: Path):
    database = build(tmp_path)
    with database.connect() as connection:
        # Stamp a version only a future build would use.
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        connection.commit()

    with pytest.raises(UnsupportedSchemaVersion) as error:
        with database.connect():
            pass

    message = str(error.value)
    assert "newer version" in message
    assert str(SCHEMA_VERSION + 5) in message
    # The stamp was not rewritten: refusing is the whole point.
    import sqlite3

    raw = sqlite3.connect(database.path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 5
    finally:
        raw.close()


def test_diagnostics_explain_a_refusal_instead_of_failing(tmp_path: Path):
    database = build(tmp_path)
    with database.connect() as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        connection.commit()

    statistics = collect(database)
    assert statistics.error is not None
    assert "newer version" in statistics.error

    report = check(database)
    assert report.status == "fatal"
    assert "newer version" in report.by_name("database").detail


def test_cli_search_explains_a_refusal_without_a_traceback(
    tmp_path: Path, monkeypatch, capsys
):
    import unittest.mock as mock

    from universal_search.cli import main

    database = build(tmp_path)
    with database.connect() as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        connection.commit()

    argv = ["universal-search", "search", "bjt", "--database", str(database.path)]
    with mock.patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as exit_info:
            main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "newer version" in captured.err
    assert "Traceback" not in captured.err


# -- backup -------------------------------------------------------------------

def test_backup_is_a_usable_copy_and_refuses_to_overwrite(tmp_path: Path):
    database = build(tmp_path)
    destination = tmp_path / "copia.db"

    backup = database.backup(destination)
    assert backup == destination

    restored = SearchDatabase(backup)
    assert [r.name for r in SearchEngine(restored).search("bjt")]
    assert restored.migration_history() == database.migration_history()

    with pytest.raises(FileExistsError):
        database.backup(destination)


def test_full_rebuild_can_keep_a_backup(tmp_path: Path):
    database = build(tmp_path)
    backup_path = tmp_path / "antes.db"

    result = rebuild_all(
        database, [tmp_path / "tree"], confirm=True, backup=backup_path
    )

    assert "backup at" in result.detail
    restored = SearchDatabase(backup_path)
    names = {r.name for r in SearchEngine(restored).search("bjt")}
    assert names == {"doc-0.md", "doc-1.md", "doc-2.md"}
    # The live index is rebuilt, not merely copied back.
    assert collect(database).documents == 3


def test_full_rebuild_without_backup_says_so(tmp_path: Path):
    database = build(tmp_path)
    result = rebuild_all(database, [tmp_path / "tree"], confirm=True)
    assert "no backup taken" in result.detail


# -- interrupted work and recovery ---------------------------------------------

def test_an_interrupted_pass_is_reconciled_by_the_next_one(tmp_path: Path):
    """A half-finished indexing pass must leave a consistent index."""
    from universal_search.providers.local import discover_local

    database = SearchDatabase(tmp_path / "index.db")
    tree = tmp_path / "tree"
    tree.mkdir()
    for index in range(6):
        (tree / f"file-{index}.md").write_text(
            f"documento {index} con bjt", encoding="utf-8"
        )

    class InterruptedIndexer(Indexer):
        """A pass that commits two documents and then dies."""

        def index_root(self, root, **kwargs):
            for document in list(discover_local(root))[:2]:
                self.upsert(document)
            raise KeyboardInterrupt("power cut")

    with pytest.raises(KeyboardInterrupt):
        InterruptedIndexer(database).index_root(tree)

    # Whatever landed is searchable, and the next full pass completes the
    # job without duplicates.
    partial = {r.name for r in SearchEngine(database).search("bjt")}
    assert partial  # some documents were committed before the interruption

    stats = Indexer(database).index_root(tree)
    final = {r.name for r in SearchEngine(database).search("bjt")}
    assert final == {f"file-{index}.md" for index in range(6)}
    assert stats.created >= 4
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE name LIKE 'file-%'"
        ).fetchone()[0]
    assert rows == 6  # no duplicates from the interrupted pass


def _terminate(pid: int) -> None:
    """Kill a process by pid and wait until it is really gone.

    Windows has no ``SIGTERM`` for ``os.kill`` (``WinError 87``), so the
    platform's own tool is used there; elsewhere signals do the job. The
    application never kills the worker this way — this stands in for a
    crash or a user ending the process.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:  # pragma: no cover - exercised on POSIX
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.05)


def test_a_killed_worker_leaves_a_stale_lock_that_a_new_one_recovers(
    tmp_path: Path,
):
    """Process termination: the next start must not refuse forever.

    The worker is killed by the pid it published, not by the pid this
    test spawned: on some Windows Python installations the venv launcher
    re-executes the interpreter, so ``Popen.pid`` and ``os.getpid()``
    differ. The property under test is about the lock, not the launcher.
    """
    from universal_search import background
    from universal_search.appconfig import AppConfig, AppPaths

    paths = AppPaths.discover(home=tmp_path / "home")
    paths.ensure()
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "nota.md").write_text("contenido con bjt", encoding="utf-8")
    AppConfig(roots=[str(tree)]).save(paths)

    child = subprocess.Popen(
        [sys.executable, "-m", "universal_search.cli", "indexer", "run"],
        env={**os.environ, "UNIVERSAL_SEARCH_HOME": str(paths.home)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        worker_pid = None
        while time.monotonic() < deadline:
            worker_pid = background.read_lock_pid(paths)
            if worker_pid is not None:
                break
            if child.poll() is not None:  # pragma: no cover - early exit
                pytest.fail("the worker exited before publishing its lock")
            time.sleep(0.05)
        assert worker_pid is not None
        assert background.process_alive(worker_pid)

        # Process termination: no clean shutdown, no status cleanup.
        _terminate(worker_pid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and background.process_alive(worker_pid):
            time.sleep(0.05)

        # The lock file survives, but its owner is gone: stale, not blocking.
        assert background.read_lock_pid(paths) == worker_pid
        assert not background.process_alive(worker_pid)

        state, message = background.start(paths)
        assert state == "started", message
        assert background.read_lock_pid(paths) != worker_pid
    finally:
        try:
            _terminate(worker_pid) if worker_pid else None
        finally:
            background.stop(paths, timeout=15.0)
            if child.poll() is None:
                _terminate(child.pid)


def test_started_worker_uses_the_home_it_was_given(tmp_path: Path):
    """A worker started with custom paths must not touch the real user home.

    Regression: ``start(paths)`` passed only ``cwd``, so the child resolved
    the *default* home and published its lock and status there — invisible
    to the caller that asked for another home. The child now inherits
    ``UNIVERSAL_SEARCH_HOME``.
    """
    from universal_search import background
    from universal_search.appconfig import AppConfig, AppPaths

    paths = AppPaths.discover(home=tmp_path / "home")
    paths.ensure()
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "nota.md").write_text("contenido con bjt", encoding="utf-8")
    AppConfig(roots=[str(tree)]).save(paths)

    state, message = background.start(paths)
    worker_pid = background.read_lock_pid(paths)
    try:
        assert state == "started", message
        assert worker_pid is not None
        # The lock is in the home we passed...
        assert paths.lock_file.exists()
        # ...and the worker really answers there. `start()` returns when the
        # lock appears, which is just before the first status write, so the
        # status is awaited rather than assumed.
        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline:
            status = background.read_status(paths)
            if status is not None and status.get("pid") == worker_pid:
                break
            time.sleep(0.05)
        assert status is not None and status["pid"] == worker_pid
        # Nothing was written to the real per-user home during this test.
        real_lock = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Universal Search" / "indexer.lock"
        )
        assert not real_lock.exists() or background.process_alive(
            int(real_lock.read_text(encoding="utf-8").strip() or 0)
        )
    finally:
        background.stop(paths, timeout=15.0)


def test_version_is_reported_by_the_cli_and_the_diagnostics(tmp_path: Path):
    database = build(tmp_path)
    statistics = collect(database)
    assert statistics.app_version == __version__
    # The ledger, the stamp and the version are all visible together.
    payload = statistics.as_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["expected_schema_version"] == SCHEMA_VERSION
    assert payload["migration_history"] == [
        {"version": version, "applied_at": applied}
        for version, applied in database.migration_history()
    ]


def test_diagnostics_report_is_stable_json(tmp_path: Path):
    database = build(tmp_path)
    payload = json.dumps(collect(database).as_dict())
    assert '"leaves_machine"' not in payload  # privacy inventory is separate
    assert "migration_history" in payload
