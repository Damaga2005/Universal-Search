"""Generation-aware worker ownership and race regressions for phase 021."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from universal_search import background
from universal_search.appconfig import AppConfig, AppPaths
from universal_search.background import WorkerAlreadyRunning, WorkerLease, WorkerOwner


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(indexer_interval_seconds=9999).save(paths)
    return paths


def write_owner(paths: AppPaths, owner: WorkerOwner) -> None:
    payload = {"pid": owner.pid, "generation": owner.generation}
    creation_id = getattr(owner, "creation_id", None)
    if creation_id is not None:
        payload["creation_id"] = creation_id
    paths.worker_owner_file.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_lock_lease_records_generation_without_changing_pid_contract(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    monkeypatch.setenv(background.WORKER_GENERATION_ENV, "generation-a")

    pid = background.acquire_lock(paths)

    assert pid == os.getpid()
    assert paths.lock_file.read_text(encoding="ascii") == str(os.getpid())
    owner = background.read_worker_owner(paths)
    assert owner is not None
    assert owner.pid == os.getpid()
    assert owner.generation == "generation-a"
    if sys.platform == "win32":
        assert owner.creation_id
    else:
        assert owner.creation_id is None
    assert paths.worker_lease_file.exists()
    background.write_status(paths, "idle")
    assert background.read_status(paths)["generation"] == "generation-a"

    background.release_lock(paths)
    assert not paths.lock_file.exists()
    assert background.read_worker_owner(paths) is None
    assert paths.worker_lease_file.exists()


def test_worker_lease_serializes_stale_handoff_between_claimers(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    first = WorkerLease(paths)
    second = WorkerLease(paths)

    first_owner = first.acquire("generation-a")
    with pytest.raises(WorkerAlreadyRunning):
        second.acquire("generation-b")

    first.release()
    second_owner = second.acquire("generation-b")
    assert first_owner.generation == "generation-a"
    assert second_owner.generation == "generation-b"
    assert background.read_lock_pid(paths) == os.getpid()
    second.release()


def test_worker_lease_releases_even_if_owner_file_cleanup_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    lease = WorkerLease(paths)
    lease.acquire("generation-a")
    original_unlink = Path.unlink

    def deny_owner_unlink(path: Path, *args, **kwargs):
        if path == paths.worker_owner_file:
            raise PermissionError("cleanup denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_owner_unlink)

    lease.release()
    monkeypatch.undo()
    contender = WorkerLease(paths)
    replacement = contender.acquire("generation-b")
    try:
        assert replacement.generation == "generation-b"
    finally:
        contender.release()


def test_stale_generation_handoff_does_not_consume_old_stop_marker(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    old = WorkerOwner(4242, "generation-old")
    old_stop = paths.stop_request_file(old.generation)
    paths.lock_file.write_text(str(old.pid), encoding="ascii")
    write_owner(paths, old)
    background.request_stop(paths, owner=old)

    monkeypatch.setenv(background.WORKER_GENERATION_ENV, "generation-new")
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)
    background.acquire_lock(paths)
    new = background.read_worker_owner(paths)

    assert new is not None
    assert new.pid == os.getpid()
    assert new.generation == "generation-new"
    assert old_stop.exists()
    assert background.stop_requested(paths, owner=new) is False
    background.clear_stop(paths, owner=new)
    assert old_stop.exists()

    background.release_lock(paths)
    old_stop.unlink()


def test_stop_marker_is_visible_only_to_its_exact_generation(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    old = WorkerOwner(111, "generation-old")
    new = WorkerOwner(111, "generation-new")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, new)

    old_stop = paths.stop_request_file(old.generation)
    background.request_stop(paths, owner=old)

    assert background.stop_requested(paths, owner=old) is True
    assert background.stop_requested(paths, owner=new) is False
    background.clear_stop(paths, owner=new)
    assert old_stop.exists()
    assert not paths.stop_file.exists()
    background.clear_stop(paths, owner=old)
    assert not old_stop.exists()


def test_stop_request_and_clear_support_explicit_generation_selector(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    owner = WorkerOwner(111, "generation-selected")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, owner)

    background.request_stop(
        paths,
        expected_generation="generation-selected",
    )

    selected = paths.stop_request_file("generation-selected")
    assert selected.exists()
    assert not paths.stop_file.exists()
    background.clear_stop(paths, expected_generation="generation-other")
    assert selected.exists()
    background.clear_stop(paths, expected_generation="generation-selected")
    assert not selected.exists()


def test_modern_stop_cleanup_cannot_unlink_replacement_generation_marker(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    old = WorkerOwner(111, "generation-old")
    replacement = WorkerOwner(111, "generation-new")
    old_path = paths.stop_request_file(old.generation)
    replacement_path = paths.stop_request_file(replacement.generation)
    background.request_stop(paths, owner=old)

    legacy_unlink_entered = threading.Event()
    continue_legacy_unlink = threading.Event()
    original_unlink = Path.unlink

    def interleave_legacy_unlink(path: Path, *args, **kwargs):
        if path == paths.stop_file:
            legacy_unlink_entered.set()
            assert continue_legacy_unlink.wait(timeout=2)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interleave_legacy_unlink)
    cleanup = threading.Thread(
        target=background.clear_stop,
        args=(paths,),
        kwargs={"owner": old},
    )
    cleanup.start()
    legacy_unlink_entered.wait(timeout=0.5)
    background.request_stop(paths, owner=replacement)
    continue_legacy_unlink.set()
    cleanup.join(timeout=2)

    assert not cleanup.is_alive()
    assert not old_path.exists()
    assert replacement_path.exists()
    assert background.stop_requested(paths, owner=replacement) is True
    assert not paths.stop_file.exists()


def test_generationless_stop_keeps_legacy_generic_marker(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    legacy = WorkerOwner(111, None)

    background.request_stop(paths, owner=legacy)

    assert paths.stop_file.exists()
    assert background.stop_requested(paths, owner=legacy) is True
    assert not paths.stop_request_file("modern").exists()
    background.clear_stop(paths, owner=legacy)
    assert not paths.stop_file.exists()


def test_expected_generation_stop_rejects_same_pid_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    replacement = WorkerOwner(111, "generation-new")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, replacement)
    requested: list[WorkerOwner] = []
    opened: list[int] = []
    terminated: list[int] = []
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(background, "request_stop", lambda _paths, *, owner: requested.append(owner))
    monkeypatch.setattr(
        background,
        "open_process_identity",
        lambda pid: opened.append(pid) or None,
    )
    monkeypatch.setattr(
        background,
        "terminate_process_identity",
        lambda identity: terminated.append(identity.pid),
    )

    state, message = background.stop(
        paths,
        timeout=0,
        expected_pid=111,
        expected_generation="generation-old",
    )

    assert state == "replaced"
    assert "reemplaz" in message
    assert requested == []
    assert opened == []
    assert terminated == []


def test_force_termination_rejects_handle_from_reused_pid_before_requesting_stop(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    expected = WorkerOwner(111, "generation-old", "creation-old")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, expected)

    class ReplacementIdentity:
        pid = 111
        creation_id = "creation-new"

        @staticmethod
        def alive() -> bool:
            return True

        @staticmethod
        def close() -> None:
            return None

    requested: list[WorkerOwner] = []
    terminated: list[int] = []
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        background,
        "request_stop",
        lambda _paths, *, owner: requested.append(owner),
    )
    monkeypatch.setattr(
        background,
        "open_process_identity",
        lambda _pid: ReplacementIdentity(),
    )
    monkeypatch.setattr(
        background,
        "terminate_process_identity",
        lambda identity: terminated.append(identity.pid) or True,
    )

    state, message = background.stop(
        paths,
        timeout=0,
        expected_pid=111,
        expected_generation="generation-old",
    )

    assert state == "replaced"
    assert "identidad" in message.lower() or "reemplaz" in message.lower()
    assert requested == []
    assert terminated == []


def test_force_termination_declines_owner_without_creation_identity(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    legacy_sidecar = WorkerOwner(111, "generation-old")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, legacy_sidecar)

    class UnboundIdentity:
        pid = 111
        creation_id = None

        @staticmethod
        def alive() -> bool:
            return True

        @staticmethod
        def close() -> None:
            return None

    terminated: list[int] = []
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        background,
        "open_process_identity",
        lambda _pid: UnboundIdentity(),
    )
    monkeypatch.setattr(
        background,
        "terminate_process_identity",
        lambda identity: terminated.append(identity.pid) or True,
    )

    state, message = background.stop(
        paths,
        timeout=0,
        expected_pid=111,
        expected_generation="generation-old",
    )

    assert state == "failed"
    assert "identidad" in message.lower() or "seguro" in message.lower()
    assert terminated == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process creation identity")
def test_windows_process_identity_exposes_stable_creation_id() -> None:
    from universal_search.platforms.worker import open_process_identity

    first = open_process_identity(os.getpid())
    second = open_process_identity(os.getpid())
    try:
        assert first is not None
        assert second is not None
        assert first.creation_id
        assert first.creation_id == second.creation_id
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()


def test_status_report_hides_current_fields_from_another_generation(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, WorkerOwner(111, "generation-new"))
    paths.status_file.write_text(
        json.dumps(
            {
                "state": "error",
                "pid": 111,
                "generation": "generation-old",
                "updated_at": "old-time",
                "roots": 7,
                "error": "old failure",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)

    report = background.status_report(paths)

    assert "iniciando" in report
    assert "old-time" not in report
    assert "carpetas: 7" not in report
    assert "old failure" not in report


def test_stop_waits_for_owned_process_exit_after_its_lock_disappears(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    owner = WorkerOwner(111, "generation-a")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, owner)
    alive_results = iter((True, False))
    alive_calls: list[bool] = []

    class FakeIdentity:
        pid = 111

        def alive(self) -> bool:
            result = next(alive_results)
            alive_calls.append(result)
            return result

        @staticmethod
        def close() -> None:
            return None

    owner_reads = 0

    def observed_owner(_paths):
        nonlocal owner_reads
        owner_reads += 1
        return owner if owner_reads <= 2 else None

    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(background, "current_worker_owner", observed_owner)
    monkeypatch.setattr(background, "open_process_identity", lambda _pid: FakeIdentity())
    monkeypatch.setattr(background.time, "sleep", lambda _seconds: None)

    state, _message = background.stop(
        paths,
        timeout=1,
        expected_pid=111,
        expected_generation="generation-a",
    )

    assert state == "stopped"
    assert alive_calls == [True, False]


def test_force_termination_uses_opened_identity_and_detects_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    original = WorkerOwner(111, "generation-old", "creation-old")
    replacement = WorkerOwner(111, "generation-new", "creation-new")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, original)

    class FakeIdentity:
        pid = 111
        kind = "windows"
        creation_id = "creation-old"

        @staticmethod
        def alive() -> bool:
            return True

        @staticmethod
        def close() -> None:
            return None

    identity = FakeIdentity()
    terminated: list[int] = []

    def terminate(selected) -> bool:
        terminated.append(selected.pid)
        paths.lock_file.write_text("111", encoding="ascii")
        write_owner(paths, replacement)
        return True

    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(background, "open_process_identity", lambda _pid: identity)
    monkeypatch.setattr(background, "terminate_process_identity", terminate)

    state, message = background.stop(
        paths,
        timeout=0,
        expected_pid=111,
        expected_generation="generation-old",
    )

    assert state == "replaced"
    assert "reemplaz" in message
    assert terminated == [111]
    assert background.read_worker_owner(paths) == replacement
    assert background.stop_requested(paths, owner=original) is True
    assert background.stop_requested(paths, owner=replacement) is False


def test_force_termination_rechecks_replacement_opened_before_identity_use(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    original = WorkerOwner(111, "generation-old", "creation-old")
    replacement = WorkerOwner(111, "generation-new", "creation-new")
    paths.lock_file.write_text("111", encoding="ascii")
    write_owner(paths, original)

    class ReplacementIdentity:
        pid = 111
        kind = "windows"
        creation_id = "creation-new"

        @staticmethod
        def alive() -> bool:
            return True

        @staticmethod
        def close() -> None:
            return None

    def open_replacement(_pid: int):
        paths.lock_file.write_text("111", encoding="ascii")
        write_owner(paths, replacement)
        return ReplacementIdentity()

    terminated: list[int] = []
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(background, "open_process_identity", open_replacement)
    monkeypatch.setattr(
        background,
        "terminate_process_identity",
        lambda identity: terminated.append(identity.pid) or True,
    )

    state, message = background.stop(
        paths,
        timeout=0,
        expected_pid=111,
        expected_generation="generation-old",
    )

    assert state == "replaced"
    assert "reemplaz" in message
    assert terminated == []
    assert not background.stop_requested(paths, owner=original)


@pytest.mark.parametrize("failure_stage", ["ensure", "write"])
def test_start_claim_filesystem_failure_is_a_bounded_failed_result(
    tmp_path: Path, monkeypatch, failure_stage: str
) -> None:
    paths = make_paths(tmp_path)

    def deny_claim(_paths, _claim) -> None:
        raise PermissionError("denied")

    def deny_ensure(_paths) -> None:
        raise PermissionError("denied")

    if failure_stage == "ensure":
        monkeypatch.setattr(AppPaths, "ensure", deny_ensure)
    else:
        monkeypatch.setattr(background, "_write_startup_claim", deny_claim)
    monkeypatch.setattr(
        background.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not spawn without a startup claim"),
    )

    state, message = background.start(paths, wait=0)

    assert state == "failed"
    assert "arranque" in message


def test_claim_replace_failure_removes_only_its_scoped_publication_files(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    generation = "generation-replace-failed"
    own_temporary = paths.startup_claim_temporary_file_for(os.getpid(), generation)
    own_final = paths.startup_claim_file_for(os.getpid(), generation)
    unrelated = paths.startup_claim_file_for(os.getpid() + 1, "generation-unrelated")

    def fail_replace(source: Path, destination: Path) -> None:
        assert source == own_temporary
        assert destination == own_final
        unrelated.write_text(
            json.dumps({"pid": os.getpid() + 1, "generation": "generation-unrelated"}),
            encoding="utf-8",
        )
        raise OSError("replace denied")

    monkeypatch.setattr(background.os, "replace", fail_replace)

    state, message = background._acquire_startup_claim(paths, generation)

    assert state == "failed"
    assert "arranque" in message
    assert not own_temporary.exists()
    assert not own_final.exists()
    assert unrelated.exists()


def test_denied_claim_cleanup_retries_and_fails_closed_for_exact_owner(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    generation = "generation-cleanup-denied"
    own_final = paths.startup_claim_file_for(os.getpid(), generation)
    own_temporary = paths.startup_claim_temporary_file_for(os.getpid(), generation)
    unrelated = paths.startup_claim_file_for(os.getpid() + 1, "generation-unrelated")
    original_unlink = Path.unlink
    denied_attempts = 0

    def deny_own_unlink(path: Path, *args, **kwargs):
        nonlocal denied_attempts
        if path == own_final:
            denied_attempts += 1
            raise PermissionError("delete denied")
        return original_unlink(path, *args, **kwargs)

    def fail_spawn(*_args, **_kwargs):
        unrelated.write_text(
            json.dumps({"pid": os.getpid() + 1, "generation": "generation-unrelated"}),
            encoding="utf-8",
        )
        raise OSError("spawn denied")

    monkeypatch.setattr(Path, "unlink", deny_own_unlink)
    monkeypatch.setattr(background.subprocess, "Popen", fail_spawn)

    state, message = background.start(
        paths,
        wait=0,
        generation=generation,
    )

    assert state == "failed"
    assert "reclamación" in message.lower()
    assert "limpiar" in message.lower()
    assert denied_attempts == 3
    assert own_final.exists()
    assert not own_temporary.exists()
    assert unrelated.exists()


def test_partial_claim_from_dead_starter_is_recovered_by_pid_scoped_path(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    dead_pid = 999_999_999
    partial = paths.startup_claim_file_for(dead_pid, "generation-crashed")
    partial.write_text('{"pid":', encoding="utf-8")
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)

    state, message = background._acquire_startup_claim(
        paths,
        "generation-recovered",
    )

    assert state == "claimed"
    assert "reclam" in message
    assert not partial.exists()
    winner = paths.startup_claim_file_for(os.getpid(), "generation-recovered")
    assert winner.exists()
    assert background.read_startup_claim(paths) == WorkerOwner(
        os.getpid(), "generation-recovered"
    )
    assert not paths.startup_claim_temporary_file_for(
        os.getpid(), "generation-recovered"
    ).exists()
    background._clear_startup_claim(paths, "generation-recovered")


def test_partial_claim_from_live_starter_is_never_removed(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    partial = paths.startup_claim_file_for(os.getpid(), "generation-live")
    partial.write_text('{"pid":', encoding="utf-8")
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)

    state, _message = background._acquire_startup_claim(
        paths,
        "generation-contender",
    )

    assert state == "busy"
    assert partial.exists()
    assert not paths.startup_claim_file_for(
        os.getpid(), "generation-contender"
    ).exists()
    partial.unlink()


def test_crash_before_atomic_claim_replace_leaves_recoverable_temp_identity(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    dead_pid = 999_999_998
    partial_temp = paths.startup_claim_temporary_file_for(
        dead_pid, "generation-crashed"
    )
    partial_temp.write_text('{"pid":', encoding="utf-8")
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)

    state, _message = background._acquire_startup_claim(
        paths,
        "generation-recovered",
    )

    assert state == "claimed"
    assert not partial_temp.exists()
    assert paths.startup_claim_file_for(
        os.getpid(), "generation-recovered"
    ).exists()
    background._clear_startup_claim(paths, "generation-recovered")


def test_losing_starter_cleanup_preserves_newer_winner_claim(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    dead_pid = 999_999_997
    stale = paths.startup_claim_file_for(dead_pid, "generation-loser")
    stale.write_text(
        json.dumps({"pid": dead_pid, "generation": "generation-loser"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)

    state, _message = background._acquire_startup_claim(
        paths,
        "generation-winner",
    )
    background._clear_startup_claim(paths, "generation-loser")

    assert state == "claimed"
    winner = paths.startup_claim_file_for(os.getpid(), "generation-winner")
    assert winner.exists()
    background._clear_startup_claim(paths, "generation-winner")


def test_child_losing_preclaim_race_removes_starter_claim_on_early_exit(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    replacement = WorkerOwner(222, "generation-winner", "creation-winner")
    owner_reads = iter((None, replacement))

    class FakeProcess:
        pid = os.getpid()
        returncode = 0

        @staticmethod
        def poll() -> int:
            return 0

    monkeypatch.setattr(
        background,
        "current_worker_owner",
        lambda _paths: next(owner_reads),
    )
    monkeypatch.setattr(background, "process_alive", lambda _pid: True)
    monkeypatch.setattr(
        background.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    state, message = background.start(
        paths,
        wait=1,
        generation="generation-loser",
    )

    assert state == "already-running"
    assert "222" in message
    assert not paths.startup_claim_file_for(
        os.getpid(), "generation-loser"
    ).exists()


def test_spawn_failure_removes_only_its_scoped_startup_claim(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)

    def fail_spawn(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(background.subprocess, "Popen", fail_spawn)
    state, message = background.start(
        paths,
        wait=0,
        generation="generation-failed-spawn",
    )

    assert state == "failed"
    assert "iniciar" in message
    assert not paths.startup_claim_file_for(
        os.getpid(), "generation-failed-spawn"
    ).exists()
    assert not list(paths.home.glob("indexer.starting.*.claim"))


def test_startup_claim_race_leaves_exactly_one_winner_generation(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    barrier = threading.Barrier(2)

    def claim(generation: str) -> tuple[str, str]:
        barrier.wait(timeout=5)
        return background._acquire_startup_claim(paths, generation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim, "generation-a")
        second = pool.submit(claim, "generation-b")
        results = [first.result(timeout=10), second.result(timeout=10)]

    assert sorted(state for state, _message in results) == ["busy", "claimed"]
    claims = sorted(paths.home.glob("indexer.starting.*.claim"))
    assert len(claims) == 1
    payload = json.loads(claims[0].read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["generation"] in {"generation-a", "generation-b"}
    background._clear_startup_claim(paths, payload["generation"])


def test_concurrent_starts_only_claim_the_generation_they_presented(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    barrier = threading.Barrier(2)

    def start(generation: str) -> tuple[str, str]:
        barrier.wait(timeout=5)
        return background.start(paths, generation=generation, wait=15)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(start, "generation-a")
            second = pool.submit(start, "generation-b")
            results = [first.result(timeout=20), second.result(timeout=20)]

        states = sorted(state for state, _message in results)
        assert states == ["already-running", "started"]

        observed: list[tuple[WorkerOwner, dict]] = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not observed:
            owner = background.read_worker_owner(paths)
            status = background.read_status(paths)
            if owner is not None and status is not None:
                observed.append((owner, status))
            else:
                time.sleep(0.02)

        assert observed, "owner/status did not become readable"
        owner, status = observed[-1]
        assert owner.generation in {"generation-a", "generation-b"}
        assert status["pid"] == owner.pid
        assert status["generation"] == owner.generation
    finally:
        background.stop(paths, timeout=5)


def test_worker_lease_is_released_when_owner_process_dies(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    script = (
        "from pathlib import Path; import sys, time; "
        "from universal_search.appconfig import AppPaths; "
        "from universal_search.background import WorkerLease; "
        "lease = WorkerLease(AppPaths(Path(sys.argv[1]))); "
        "owner = lease.acquire('child-generation'); "
        "print(owner.pid, flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(paths.home)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        child_pid = int(process.stdout.readline().strip())
        assert child_pid == background.read_lock_pid(paths)
        contender = WorkerLease(paths)
        with pytest.raises(WorkerAlreadyRunning):
            contender.acquire("replacement-generation")

        process.terminate()
        process.wait(timeout=5)
        state, _message = background.stop(
            paths,
            timeout=0,
            expected_pid=child_pid,
            expected_generation="child-generation",
        )
        assert state in {"stopped", "not-running"}
        replacement = contender.acquire("replacement-generation")
        assert replacement.pid == os.getpid()
        contender.release()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for path in (paths.lock_file, paths.worker_owner_file, paths.stop_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
