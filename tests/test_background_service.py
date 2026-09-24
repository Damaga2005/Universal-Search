"""Phase 021: platform-independent background state and actions."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_search import background
from universal_search.appconfig import AppPaths
from universal_search.background_service import (
    BackgroundService,
    BackgroundStatus,
    run_completed_long_ago,
    should_notify,
)


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    return paths


WORKER_GENERATION = "worker-generation"


def write_payload(paths: AppPaths, **payload: object) -> None:
    payload.setdefault("generation", WORKER_GENERATION)
    paths.status_file.write_text(json.dumps(payload), encoding="utf-8")


def configure_live_worker(
    monkeypatch, paths: AppPaths, pid: int = 4242, *, autostart: bool = False
) -> object:
    """Use deterministic liveness and registry collaborators."""
    paths.lock_file.write_text(str(pid), encoding="ascii")
    paths.worker_owner_file.write_text(
        json.dumps({"pid": pid, "generation": WORKER_GENERATION}),
        encoding="utf-8",
    )
    registry = object()
    expected_registry = registry
    monkeypatch.setattr(background, "process_alive", lambda actual: actual == pid)
    monkeypatch.setattr(
        background,
        "get_autostart",
        lambda registry=None: registry is expected_registry and autostart,
    )
    return registry


def test_status_is_starting_when_live_worker_has_not_written_state(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths, autostart=True)

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "starting"
    assert status.pid == 4242
    assert status.autostart is True
    assert status.running is True
    assert status.can_start is False
    assert status.can_stop is True
    assert status.can_pause is True
    assert status.can_resume is False
    assert "pendiente" in status.pending.lower()


def test_status_is_indexing_and_keeps_last_scan(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(
        paths,
        state="indexing",
        pid=4242,
        updated_at="2026-09-23T20:00:00+00:00",
        last_scan_at="2026-09-23T19:58:00+00:00",
        last_scan_stats={"created": 2, "updated": 1},
        roots=3,
    )

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "indexing"
    assert status.last_scan_at == "2026-09-23T19:58:00+00:00"
    assert status.last_scan_stats == {"created": 2, "updated": 1}
    assert status.roots == 3
    assert status.can_pause is True
    assert status.can_stop is True
    assert "pasada" in status.pending.lower() or "curso" in status.pending.lower()


def test_status_ignores_state_from_a_different_or_missing_worker_pid(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    service = BackgroundService(paths, registry=registry)

    write_payload(paths, state="indexing", roots=1)
    assert service.status().state == "starting"

    write_payload(paths, state="indexing", pid=999999, roots=1)
    assert service.status().state == "starting"

    write_payload(paths, state="indexing", pid=4242, roots=1)
    assert service.status().state == "indexing"


def test_same_pid_different_generation_hides_all_stale_current_fields(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(
        paths,
        state="error",
        pid=4242,
        generation="generation-old",
        updated_at="2026-09-23T20:00:00+00:00",
        error="old failure",
        hotkey="old hotkey failure",
        roots=9,
        last_scan_at="2026-09-23T19:00:00+00:00",
        last_scan_stats={"created": 4},
    )

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "starting"
    assert status.pid == 4242
    assert status.generation is None
    assert status.updated_at is None
    assert status.roots == 0
    assert status.error is None
    assert status.hotkey_error is None
    assert status.last_scan_at == "2026-09-23T19:00:00+00:00"
    assert status.last_scan_stats == {"created": 4}
    assert status.as_dict()["generation"] is None


def test_matching_generation_exposes_current_fields(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(
        paths,
        state="error",
        pid=4242,
        updated_at="2026-09-23T20:00:00+00:00",
        error="current failure",
        hotkey="current hotkey failure",
        roots=3,
    )

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "error"
    assert status.generation == WORKER_GENERATION
    assert status.updated_at == "2026-09-23T20:00:00+00:00"
    assert status.roots == 3
    assert status.error == "current failure"
    assert status.hotkey_error == "current hotkey failure"


def test_legacy_pid_and_pidless_status_remain_readable(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    paths.lock_file.write_text("4242", encoding="ascii")
    paths.status_file.write_text(
        json.dumps(
            {
                "state": "idle",
                "pid": 4242,
                "updated_at": "2026-09-23T20:00:00+00:00",
                "roots": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(background, "get_autostart", lambda **_kwargs: False)

    status = BackgroundService(paths).status()

    assert status.state == "idle"
    assert status.pid == 4242
    assert status.generation is None
    assert status.roots == 1


def test_status_is_paused_when_marker_exists(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(paths, state="indexing", pid=4242, roots=1)
    paths.pause_file.touch()

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "paused"
    assert status.paused is True
    assert status.can_pause is False
    assert status.can_resume is True
    assert status.can_stop is True
    assert "pausa" in status.pending.lower()


def test_status_is_stopping_when_stop_marker_and_live_worker(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(paths, state="indexing", pid=4242, roots=1)
    paths.stop_request_file(WORKER_GENERATION).touch()

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "stopping"
    assert status.can_start is False
    assert status.can_stop is False
    assert status.can_pause is False
    assert status.can_resume is False
    assert "termin" in status.pending.lower()


def test_status_is_error_from_worker_payload(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    write_payload(
        paths,
        state="error",
        pid=4242,
        error="ValueError: no se pudo leer",
        updated_at="2026-09-23T20:00:00+00:00",
        roots=2,
    )

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "error"
    assert status.error == "ValueError: no se pudo leer"
    assert status.can_stop is True
    assert status.can_pause is False
    assert "error" in status.pending.lower()


def test_dead_pid_is_stopped_with_stale_lock_problem(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    paths.lock_file.write_text("999999", encoding="ascii")
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)
    monkeypatch.setattr(background, "get_autostart", lambda **_kwargs: False)
    write_payload(paths, state="indexing", pid=999999, roots=4)

    status = BackgroundService(paths).status()

    assert status.state == "stopped"
    assert status.pid is None
    assert status.stale_lock is True
    assert status.problem is not None
    assert "999999" in status.problem
    assert status.can_start is True
    assert status.can_stop is False
    assert status.can_pause is False
    assert status.can_resume is False
    assert "detenido" in status.summary.lower() or "bloqueo" in status.problem.lower()


def test_corrupt_status_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    registry = configure_live_worker(monkeypatch, paths)
    paths.status_file.write_text("{not valid json", encoding="utf-8")

    status = BackgroundService(paths, registry=registry).status()

    assert status.state == "starting"
    assert status.error is None
    assert status.last_scan_at is None
    assert status.last_scan_stats is None
    assert status.problem is not None


def test_corrupt_lock_exposes_a_problem_without_raising(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    paths.lock_file.write_text("not-a-pid", encoding="ascii")
    monkeypatch.setattr(background, "get_autostart", lambda **_kwargs: False)

    status = BackgroundService(paths).status()

    assert status.state == "stopped"
    assert status.stale_lock is False
    assert status.problem is not None
    assert "bloqueo" in status.problem.lower()


def test_status_is_defensive_when_coordination_reads_raise(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("coordination file unavailable")

    monkeypatch.setattr(background, "read_status", broken)
    monkeypatch.setattr(background, "read_lock_pid", broken)
    monkeypatch.setattr(background, "is_paused", broken)
    monkeypatch.setattr(background, "stop_requested", broken)
    monkeypatch.setattr(background, "get_autostart", broken)

    status = BackgroundService(paths).status()

    assert status.state == "stopped"
    assert status.autostart is False
    assert status.problem is not None


def test_actions_delegate_and_refuse_when_stopped(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(background, "process_alive", lambda _pid: False)
    monkeypatch.setattr(background, "get_autostart", lambda **_kwargs: False)
    monkeypatch.setattr(
        background,
        "start",
        lambda supplied, *, generation: calls.append(
            ("start", supplied, generation)
        )
        or ("started", "ok"),
    )
    monkeypatch.setattr(
        background,
        "stop",
        lambda supplied, *, timeout: calls.append(("stop", timeout)) or ("stopped", "ok"),
    )
    monkeypatch.setattr(background, "pause", lambda supplied: calls.append(("pause", supplied)))
    monkeypatch.setattr(background, "resume", lambda supplied: calls.append(("resume", supplied)))

    service = BackgroundService(paths)

    assert service.start() == ("started", "ok")
    assert service.stop(timeout=3.5) == ("stopped", "ok")
    assert service.pause()[0] == "not-running"
    assert service.resume()[0] == "not-running"
    assert calls == [("start", paths, None), ("stop", 3.5)]


def test_stop_forwards_expected_pid_to_background_service(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    calls: list[tuple[object, float, int | None, str | None]] = []
    monkeypatch.setattr(
        background,
        "stop",
        lambda supplied, *, timeout, expected_pid, expected_generation: calls.append(
            (supplied, timeout, expected_pid, expected_generation)
        )
        or ("stopped", "ok"),
    )

    result = BackgroundService(paths).stop(
        timeout=2.5,
        expected_pid=4242,
        expected_generation="generation-a",
    )

    assert result == ("stopped", "ok")
    assert calls == [(paths, 2.5, 4242, "generation-a")]


@pytest.mark.parametrize("state", ["stopping", "error"])
def test_pause_and_resume_refuse_states_without_capabilities(
    tmp_path: Path, monkeypatch, state: str
) -> None:
    paths = make_paths(tmp_path)
    configure_live_worker(monkeypatch, paths)
    if state == "stopping":
        paths.stop_request_file(WORKER_GENERATION).touch()
    write_payload(paths, state=state, pid=4242)
    calls: list[str] = []
    monkeypatch.setattr(background, "pause", lambda _paths: calls.append("pause"))
    monkeypatch.setattr(background, "resume", lambda _paths: calls.append("resume"))

    service = BackgroundService(paths)

    assert service.pause()[0] == "unavailable"
    assert service.resume()[0] == "unavailable"
    assert calls == []


def test_notification_policy_is_quiet_for_user_actions() -> None:
    def snapshot(state: str, **changes: object) -> BackgroundStatus:
        return BackgroundStatus(state=state, **changes)

    for previous, current in (
        (snapshot("stopped"), snapshot("starting")),
        (snapshot("indexing"), snapshot("paused")),
        (snapshot("paused"), snapshot("indexing")),
        (snapshot("indexing"), snapshot("stopping")),
        (snapshot("stopping"), snapshot("stopped")),
    ):
        assert should_notify(previous, current) is None

    assert should_notify(
        snapshot("indexing"), snapshot("error", error="fallo nuevo")
    ) == "fallo nuevo"
    assert should_notify(
        snapshot("indexing"), snapshot("indexing", hotkey_error="atajo ocupado")
    ) == "atajo ocupado"
    assert should_notify(
        snapshot("indexing"), snapshot("stopped", stale_lock=True)
    ) == "el indexador ha desaparecido; el bloqueo quedó obsoleto"
    for previous_state in ("indexing", "starting"):
        assert should_notify(
            snapshot(previous_state, pid=4242),
            snapshot("stopped", error="fallo del trabajador"),
        ) == "el indexador ha desaparecido"
    assert should_notify(
        snapshot("error", error="fallo nuevo"),
        snapshot("error", error="fallo nuevo"),
    ) is None
    assert should_notify(
        snapshot("error", error="fallo anterior", generation="generation-old"),
        snapshot("error", error="fallo nuevo", generation="generation-new"),
    ) == "fallo nuevo"
    assert should_notify(
        snapshot("error", error="fallo anterior"),
        snapshot("error", error="fallo nuevo"),
    ) == "fallo nuevo"
    assert should_notify(
        snapshot("indexing", hotkey_error="atajo ocupado"),
        snapshot("indexing", hotkey_error="atajo ocupado"),
    ) is None


def test_notification_policy_tracks_explicit_user_stop_origin() -> None:
    previous = BackgroundStatus(state="indexing", pid=4242)
    clean_stop = BackgroundStatus(state="stopped")
    stale_lock = BackgroundStatus(state="stopped", stale_lock=True)

    clean_reason = should_notify(previous, clean_stop)
    assert clean_reason == "el indexador ha desaparecido"
    assert clean_reason is not None
    assert "bloqueo" not in clean_reason.casefold()

    stale_reason = should_notify(previous, stale_lock)
    assert stale_reason == "el indexador ha desaparecido; el bloqueo quedó obsoleto"
    assert "bloqueo" in stale_reason.casefold()

    assert should_notify(previous, clean_stop, user_requested_stop=True) is None
    assert should_notify(previous, stale_lock, user_requested_stop=True) is None


def test_long_pass_notification_threshold_and_malformed_timestamps() -> None:
    started = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    ended = started + timedelta(seconds=120)
    indexing = BackgroundStatus(
        state="indexing",
        updated_at=started.isoformat(),
        generation="generation-a",
    )
    idle = BackgroundStatus(
        state="idle",
        updated_at=ended.isoformat(),
        generation="generation-a",
    )

    assert run_completed_long_ago(indexing, idle) is True
    assert run_completed_long_ago(
        indexing,
        replace(idle, generation="generation-b"),
    ) is False
    assert run_completed_long_ago(
        replace(indexing, generation=None),
        replace(idle, generation=None),
    ) is False
    assert run_completed_long_ago(replace(indexing, state="starting"), idle) is False
    assert run_completed_long_ago(
        indexing,
        replace(idle, updated_at=(started + timedelta(seconds=119)).isoformat()),
    ) is False
    assert run_completed_long_ago(
        replace(indexing, updated_at="not-a-timestamp"), idle
    ) is False
    assert run_completed_long_ago(indexing, replace(idle, updated_at="not-a-timestamp")) is False
