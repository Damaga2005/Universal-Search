"""Phase 006: background indexer lifecycle, status, control and limits."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from universal_search import background, cli
from universal_search.appconfig import AppConfig, AppPaths
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:  # transient states (mid-write) count as "not yet"
            pass
        time.sleep(0.1)
    return False


def make_home(tmp_path: Path, roots: tuple[str, ...] = ()) -> AppPaths:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(roots=roots, indexer_interval_seconds=9999).save(paths)
    return paths


# -- single instance lock --------------------------------------------------------

def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")

    pid = background.acquire_lock(paths)
    assert pid == os.getpid()
    assert paths.lock_file.read_text(encoding="ascii") == str(os.getpid())
    with pytest.raises(background.WorkerAlreadyRunning):
        background.acquire_lock(paths)

    background.release_lock(paths)
    assert not paths.lock_file.exists()
    background.acquire_lock(paths)  # free again
    background.release_lock(paths)


def test_stale_lock_from_dead_process_is_replaced(tmp_path, monkeypatch) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.lock_file.write_text("499999", encoding="ascii")
    monkeypatch.setattr(background, "process_alive", lambda pid: False)

    pid = background.acquire_lock(paths)

    assert pid == os.getpid()
    assert background.read_lock_pid(paths) == os.getpid()


def test_run_refuses_a_second_live_instance(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.lock_file.write_text(str(os.getpid()), encoding="ascii")  # alive: us

    worker = background.BackgroundIndexer(paths=paths, observers=False)

    assert worker.run() == background.EXIT_ALREADY_RUNNING
    assert background.read_lock_pid(paths) == os.getpid()  # untouched


def test_process_alive_for_own_pid() -> None:
    assert background.process_alive(os.getpid())
    assert not background.process_alive(-1)
    assert not background.process_alive(0)


# -- status file -------------------------------------------------------------------

def test_status_is_atomic_complete_and_tolerant(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")

    background.write_status(paths, "idle", stats={"created": 3}, roots=2)
    status = background.read_status(paths)
    assert status is not None
    assert status["state"] == "idle"
    assert status["stats"]["created"] == 3
    assert status["roots"] == 2
    assert "pid" in status and "updated_at" in status
    assert not list(paths.home.glob("*.tmp"))  # atomic: no temporary left behind

    background.write_status(paths, "indexing")
    status = background.read_status(paths)
    assert status["state"] == "indexing"
    assert "stats" not in status  # full payload, not a merge of stale fields

    background.write_status(paths, "error", error="boom")
    assert background.read_status(paths)["error"] == "boom"

    paths.status_file.write_text("{corrupt", encoding="utf-8")
    assert background.read_status(paths) is None  # never raises
    paths.status_file.write_text("[1, 2]", encoding="utf-8")
    assert background.read_status(paths) is None

    background.clear_status(paths)
    assert background.read_status(paths) is None
    background.clear_status(paths)  # idempotent


# -- pause / stop markers ----------------------------------------------------------

def test_pause_resume_and_stop_markers(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")

    assert not background.is_paused(paths)
    background.pause(paths)
    background.pause(paths)  # idempotent
    assert background.is_paused(paths)
    background.resume(paths)
    background.resume(paths)  # idempotent
    assert not background.is_paused(paths)

    assert not background.stop_requested(paths)
    background.request_stop(paths)
    assert background.stop_requested(paths)
    background.clear_stop(paths)
    background.clear_stop(paths)
    assert not background.stop_requested(paths)


def test_status_report_mentions_paused_marker_when_stopped(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    background.pause(paths)

    report = background.status_report(paths)

    assert "detenido" in report
    assert "pausa" in report


# -- autostart (registry injected) ---------------------------------------------------

class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 1
    KEY_QUERY_VALUE = 2
    REG_SZ = "REG_SZ"

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, str]] = {}

    def OpenKey(self, hive, path, reserved, access):
        return f"key:{path}"

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = (kind, value)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key):
        pass


def test_autostart_writes_run_key_via_injected_registry() -> None:
    fake = FakeRegistry()
    assert not background.get_autostart(fake)

    background.set_autostart(True, registry=fake)
    assert background.get_autostart(fake)
    kind, command = fake.values["Universal Search"]
    assert kind == "REG_SZ"
    assert "indexer" in command and "run" in command
    assert "universal_search.cli" in command  # dev command

    background.set_autostart(False, registry=fake)
    assert not background.get_autostart(fake)
    background.set_autostart(False, registry=fake)  # absent -> no error


def test_autostart_command_for_frozen_executable(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = background.autostart_command()
    assert "indexer run" in command
    assert "-m" not in command  # packaged build calls the exe directly


# -- worker: full lifecycle with real filesystem watchers -----------------------------

def test_worker_keeps_index_current_and_stops_cleanly(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "uno.md").write_text("primer documento", encoding="utf-8")
    paths = make_home(tmp_path, roots=(str(files),))

    stop_event = threading.Event()
    worker = background.BackgroundIndexer(
        paths=paths, stop_event=stop_event, observers=True
    )
    outcome: list[int] = []
    thread = threading.Thread(
        target=lambda: outcome.append(worker.run()), daemon=True
    )
    thread.start()

    # initial reconciliation + lock + status
    assert wait_until(
        lambda: (background.read_status(paths) or {}).get("state") == "idle"
        and background.read_lock_pid(paths) is not None
    ), "worker did not reach idle"
    engine = SearchEngine(SearchDatabase(paths.database))
    assert [r.name for r in engine.search("primer")] == ["uno.md"]

    # created -> indexed by the watcher
    (files / "dos.md").write_text("archivo nuevo creado", encoding="utf-8")
    assert wait_until(
        lambda: [r.name for r in engine.search("creado")] == ["dos.md"]
    ), "new file was not indexed"

    # modified -> old content replaced
    (files / "uno.md").write_text(
        "contenido modificado y totalmente distinto", encoding="utf-8"
    )
    assert wait_until(lambda: engine.search("primer") == []), "stale content kept"
    assert [r.name for r in engine.search("modificado")] == ["uno.md"]

    # deleted -> removed from the index
    (files / "dos.md").unlink()
    assert wait_until(lambda: engine.search("creado") == []), "deleted file kept"

    # graceful shutdown through the stop marker (same as stop())
    background.request_stop(paths)
    thread.join(timeout=10)
    assert not thread.is_alive(), "worker did not stop"
    assert outcome == [background.EXIT_OK]
    assert background.read_lock_pid(paths) is None
    assert background.read_status(paths) is None
    assert not background.stop_requested(paths)  # marker cleaned up


# -- worker as a real separate process ------------------------------------------------

def test_start_stop_and_no_duplicate_process(tmp_path, monkeypatch) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "n.md").write_text("documento de prueba", encoding="utf-8")
    paths = make_home(tmp_path, roots=(str(files),))
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(paths.home))  # child inherits

    try:
        state, message = background.start(paths)
        assert state == "started", message
        first_pid = background.read_lock_pid(paths)
        assert first_pid and background.process_alive(first_pid)

        # second start: no duplicate process
        state, message = background.start(paths)
        assert state == "already-running", message
        assert background.read_lock_pid(paths) == first_pid

        # the child actually indexes (parent reads the same database)
        engine = SearchEngine(SearchDatabase(paths.database))
        assert wait_until(
            lambda: [r.name for r in engine.search("prueba")] == ["n.md"]
        ), "worker did not index"

        # clean stop
        state, message = background.stop(paths)
        assert state == "stopped", message
        assert background.read_lock_pid(paths) is None
        assert background.read_status(paths) is None

        # restarting does not corrupt state
        state, message = background.start(paths)
        assert state == "started", message
    finally:
        background.stop(paths, timeout=5.0)

    assert background.read_lock_pid(paths) is None
    # the index survives the restart untouched
    engine = SearchEngine(SearchDatabase(paths.database))
    assert [r.name for r in engine.search("prueba")] == ["n.md"]


def test_stop_when_not_running_is_not_an_error(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "home")
    state, message = background.stop(paths)
    assert state == "not-running"
    assert "no está" in message


# -- resource limits --------------------------------------------------------------------

def test_index_root_delay_is_a_configurable_resource_limit(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for index in range(3):
        (files / f"doc{index}.md").write_text("texto", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")

    started = time.monotonic()
    Indexer(database).index_root(files, delay=0.05)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.09  # three writes x 50 ms of cooperative throttling


# -- CLI surface --------------------------------------------------------------------------

def run_cli(argv: list[str], monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["universal-search", *argv])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    return exit_info.value.code, capsys.readouterr().out


def test_cli_indexer_status_reports_stopped(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))

    code, output = run_cli(["indexer", "status"], monkeypatch, capsys)

    assert code == 0
    assert "detenido" in output


def test_cli_indexer_pause_resume_cycle(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(home))

    code, _ = run_cli(["indexer", "pause"], monkeypatch, capsys)
    assert code == 0
    assert (home / "indexer-paused.flag").exists()

    code, output = run_cli(["indexer", "status"], monkeypatch, capsys)
    assert code == 0
    assert "pausa" in output

    code, _ = run_cli(["indexer", "resume"], monkeypatch, capsys)
    assert code == 0
    assert not (home / "indexer-paused.flag").exists()

    code, output = run_cli(["indexer", "status"], monkeypatch, capsys)
    assert code == 0
    assert "pausa" not in output


def test_cli_indexer_autostart_status_is_read_only(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("UNIVERSAL_SEARCH_HOME", str(tmp_path / "home"))

    code, output = run_cli(["indexer", "autostart", "status"], monkeypatch, capsys)

    assert code == 0
    assert output.strip() in ("activado", "desactivado")


def test_cli_rejects_unknown_indexer_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "argv", ["universal-search", "indexer", "explode"]
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code != 0
