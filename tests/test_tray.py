"""Phase 021: platform-independent tray controller behavior."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from universal_search import tray
from universal_search.appconfig import AppPaths
from universal_search.background_service import BackgroundStatus, should_notify
from universal_search.platforms import tray as platform_tray
from universal_search.platforms.tray import MenuItem


class FakeService:
    def __init__(self, paths: AppPaths, state: str = "indexing") -> None:
        self.paths = paths
        self.calls: list[str] = []
        self.status_error: Exception | None = None
        self.start_pid: int | None = 4242
        self.start_generation: str | None = None
        self.stop_result = ("stopped", "indexador detenido")
        self.stop_error: Exception | None = None
        self.stop_expected_owners: list[tuple[int | None, str | None]] = []
        self.replacement_pid: int | None = None
        self.replacement_generation: str | None = None
        self.status_value = BackgroundStatus(
            state=state,
            pid=4242 if state not in {"stopped", "stopping"} else None,
            generation=(
                "external-generation"
                if state not in {"stopped", "stopping"}
                else None
            ),
            roots=2,
        )

    def status(self) -> BackgroundStatus:
        self.calls.append("status")
        if self.status_error is not None:
            raise self.status_error
        return self.status_value

    def _action(
        self,
        name: str,
        result: tuple[str, str],
        next_state: str,
    ) -> tuple[str, str]:
        self.calls.append(name)
        self.status_value = BackgroundStatus(
            state=next_state,
            pid=self.start_pid if next_state != "stopped" else None,
            generation=self.status_value.generation,
            roots=2,
        )
        return result

    def start(self, generation: str | None = None) -> tuple[str, str]:
        self.start_generation = generation
        result = self._action("start", ("started", "indexador iniciado"), "indexing")
        self.status_value = BackgroundStatus(
            state="indexing",
            pid=self.start_pid,
            generation=generation,
            roots=2,
        )
        return result

    def stop(
        self,
        *,
        timeout: float = 8.0,
        expected_pid: int | None = None,
        expected_generation: str | None = None,
    ) -> tuple[str, str]:
        del timeout
        self.calls.append("stop")
        self.stop_expected_owners.append((expected_pid, expected_generation))
        if self.stop_error is not None:
            raise self.stop_error
        if (
            self.replacement_pid is not None
            and expected_pid == self.start_pid
        ):
            self.status_value = BackgroundStatus(
                state="indexing",
                pid=self.replacement_pid,
                generation=self.replacement_generation or "replacement-generation",
                roots=2,
            )
            return "replaced", "el proceso fue reemplazado"
        if self.stop_result[0] in {"stopped", "not-running"}:
            self.status_value = BackgroundStatus(state="stopped", roots=2)
        return self.stop_result

    def pause(self) -> tuple[str, str]:
        return self._action("pause", ("paused", "indexación en pausa"), "paused")

    def resume(self) -> tuple[str, str]:
        return self._action("resume", ("resumed", "indexación reanudada"), "indexing")


class FakeBackend:
    def __init__(
        self,
        *,
        available: bool = True,
        run_result: int = 0,
        run_error: Exception | None = None,
        notify_error: Exception | None = None,
        update_error: Exception | None = None,
        on_run: Callable[[FakeBackend], None] | None = None,
    ) -> None:
        self._available = available
        self._run_result = run_result
        self._run_error = run_error
        self._notify_error = notify_error
        self._update_error = update_error
        self._on_run = on_run
        self.on_command = None
        self.menu = None
        self.tooltip = ""
        self.tick = None
        self.notifications: list[tuple[str, str]] = []
        self.tooltips: list[str] = []
        self.exit_requests = 0

    def available(self) -> bool:
        return self._available

    def run(self, on_command, menu, tooltip, tick=None) -> int:
        self.on_command = on_command
        self.menu = menu
        self.tooltip = tooltip
        self.tick = tick
        if self._on_run is not None:
            self._on_run(self)
        if self._run_error is not None:
            raise self._run_error
        return self._run_result

    def notify(self, title: str, message: str) -> bool:
        if self._notify_error is not None:
            raise self._notify_error
        self.notifications.append((title, message))
        return True

    def update_tooltip(self, text: str) -> bool:
        if self._update_error is not None:
            raise self._update_error
        self.tooltips.append(text)
        return True

    def request_exit(self) -> bool:
        self.exit_requests += 1
        return True

    def describe(self) -> str:
        return f"Fake tray (available: {self._available})"


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    return paths


def make_controller(
    tmp_path: Path,
    *,
    state: str = "indexing",
    backend: FakeBackend | None = None,
    lock_factory: Callable[[Path], object] | None = None,
) -> tuple[tray.TrayController, FakeService, FakeBackend, AppPaths]:
    paths = make_paths(tmp_path)
    service = FakeService(paths, state)
    selected_backend = backend or FakeBackend()
    controller_kwargs = {
        "paths": paths,
        "pid": 4242,
        "process_alive": lambda pid: pid == 111,
    }
    if lock_factory is not None:
        controller_kwargs["lock_factory"] = lock_factory
    controller = tray.TrayController(
        service,
        selected_backend,
        **controller_kwargs,
    )
    return controller, service, selected_backend, paths


def labels(menu: list[MenuItem]) -> list[str]:
    return [item.label for item in menu]


def test_menu_contains_state_open_quick_and_exit() -> None:
    status = BackgroundStatus(state="indexing", roots=2)

    menu = tray.build_menu(status)

    assert menu[0].command == 0
    assert menu[0].label == "Indexando (2 carpeta(s))"
    assert menu[0].enabled is False
    assert {
        tray.COMMAND_OPEN,
        tray.COMMAND_QUICK_SEARCH,
        tray.COMMAND_EXIT,
    }.issubset({item.command for item in menu})
    assert "Abrir Universal Search" in labels(menu)
    assert "Búsqueda rápida" in labels(menu)
    assert "Diagnóstico" in labels(menu)
    assert "Configuración" in labels(menu)
    assert "Salir" in labels(menu)


def test_menu_offers_resume_only_when_paused() -> None:
    menu = tray.build_menu(BackgroundStatus(state="paused", pid=4242))

    assert "Reanudar indexación" in labels(menu)
    assert "Pausar indexación" not in labels(menu)
    assert "Detener indexador" in labels(menu)


def test_menu_offers_start_only_when_stopped() -> None:
    menu = tray.build_menu(BackgroundStatus(state="stopped"))

    assert "Iniciar indexador" in labels(menu)
    assert "Detener indexador" not in labels(menu)
    assert "Pausar indexación" not in labels(menu)
    assert "Reanudar indexación" not in labels(menu)


def test_menu_offers_stop_and_pause_when_running() -> None:
    menu = tray.build_menu(BackgroundStatus(state="indexing", pid=4242))

    assert "Pausar indexación" in labels(menu)
    assert "Detener indexador" in labels(menu)
    assert "Iniciar indexador" not in labels(menu)
    assert "Reanudar indexación" not in labels(menu)


def test_open_reuses_live_gui_before_launching(tmp_path: Path, monkeypatch) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        tray, "request_show", lambda supplied: calls.append("request") or supplied == paths
    )
    monkeypatch.setattr(tray, "launch_gui", lambda: calls.append("launch"))

    result = controller.run_command(tray.COMMAND_OPEN)

    assert result.ok is True
    assert calls == ["request"]


def test_quick_search_uses_same_activation_path(tmp_path: Path, monkeypatch) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        tray, "request_show", lambda supplied: calls.append("request") or supplied == paths
    )
    monkeypatch.setattr(tray, "launch_gui", lambda: calls.append("launch"))

    result = controller.run_command(tray.COMMAND_QUICK_SEARCH)

    assert result.ok is True
    assert calls == ["request"]


def test_diagnostics_requests_existing_gui_view(tmp_path: Path, monkeypatch) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        tray,
        "request_diagnostics",
        lambda supplied: calls.append(("request", supplied)) or True,
    )
    monkeypatch.setattr(tray, "launch_gui", lambda: calls.append("launch"))

    result = controller.run_command(tray.COMMAND_DIAGNOSTICS)

    assert result.ok is True
    assert calls == [("request", paths)]


def test_diagnostics_launches_gui_then_leaves_request_for_next_poll(
    tmp_path: Path, monkeypatch
) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        tray, "request_diagnostics", lambda _paths: calls.append("request") or False
    )

    def launch_gui() -> None:
        calls.append("launch")
        assert not paths.diagnostics_request_file.exists()

    monkeypatch.setattr(tray, "launch_gui", launch_gui)

    result = controller.run_command(tray.COMMAND_DIAGNOSTICS)

    assert result.ok is True
    assert calls == ["request", "launch"]
    assert paths.diagnostics_request_file.exists()


def test_settings_opens_existing_window(tmp_path: Path, monkeypatch) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        tray, "request_show", lambda supplied: calls.append("request") or supplied == paths
    )
    monkeypatch.setattr(tray, "launch_gui", lambda: calls.append("launch"))

    result = controller.run_command(tray.COMMAND_SETTINGS)

    assert result.ok is True
    assert calls == ["request"]


def test_gui_commands_reuse_signaling_without_opening_sqlite(
    tmp_path: Path, monkeypatch
) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    launches: list[str] = []
    monkeypatch.setattr(tray, "request_show", lambda _paths: False)
    monkeypatch.setattr(tray, "request_diagnostics", lambda _paths: False)
    monkeypatch.setattr(
        tray, "launch_gui", lambda: launches.append("launch")
    )

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("tray GUI commands must not read SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)

    for command in (
        tray.COMMAND_OPEN,
        tray.COMMAND_QUICK_SEARCH,
        tray.COMMAND_SETTINGS,
        tray.COMMAND_DIAGNOSTICS,
    ):
        assert controller.run_command(command).ok is True

    assert len(launches) == 4
    assert not paths.database.exists()


@pytest.mark.parametrize(
    ("command", "expected_calls"),
    [
        (tray.COMMAND_PAUSE, ["pause"]),
        (tray.COMMAND_RESUME, ["resume"]),
        (tray.COMMAND_START, ["start", "status"]),
        (tray.COMMAND_STOP, ["stop"]),
    ],
)
def test_pause_resume_start_stop_map_to_service_actions(
    tmp_path: Path, command: int, expected_calls: list[str]
) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path)

    result = controller.run_command(command)

    assert result.ok is True
    assert service.calls == expected_calls


def test_unknown_command_is_rejected_without_side_effects(tmp_path: Path) -> None:
    controller, service, backend, paths = make_controller(tmp_path)

    result = controller.run_command(9999)

    assert result.ok is False
    assert service.calls == []
    assert backend.notifications == []
    assert not paths.tray_pid_file.exists()


def test_start_presents_and_retains_a_unique_worker_generation(
    tmp_path: Path,
) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path, state="stopped")

    result = controller.run_command(tray.COMMAND_START)

    assert result.ok is True
    assert service.start_generation is not None
    assert len(service.start_generation) >= 16
    assert service.status_value.generation == service.start_generation


def test_started_but_unverified_generation_is_not_claimed(tmp_path: Path) -> None:
    controller, service, backend, _paths = make_controller(tmp_path, state="stopped")

    def mismatched_start(generation: str | None) -> tuple[str, str]:
        service.calls.append("start")
        service.start_generation = generation
        service.status_value = BackgroundStatus(
            state="indexing",
            pid=4242,
            generation="another-starters-generation",
            roots=2,
        )
        return "started", "indexador iniciado"

    service.start = mismatched_start  # type: ignore[method-assign]

    result = controller.run_command(tray.COMMAND_START)

    assert result.ok is False
    assert controller.exit().ok is True
    assert service.stop_expected_owners == []
    assert backend.exit_requests == 1


def test_legacy_pid_only_worker_is_never_claimed_as_tray_owned(
    tmp_path: Path,
) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path, state="stopped")

    def legacy_start(generation: str | None) -> tuple[str, str]:
        service.calls.append("start")
        service.start_generation = generation
        service.status_value = BackgroundStatus(
            state="indexing",
            pid=4242,
            generation=None,
            roots=2,
        )
        return "already-running", "indexador ya en marcha (pid 4242)"

    service.start = legacy_start  # type: ignore[method-assign]

    result = controller.run_command(tray.COMMAND_START)

    assert result.ok is True
    assert controller.exit().ok is True
    assert service.stop_expected_owners == []


def test_claim_recovers_unlocked_stale_pid_without_deleting_diagnostics(
    tmp_path: Path,
) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    paths.tray_pid_file.write_text("333", encoding="ascii")

    assert controller.claim() == 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"

    controller.release()

    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_claim_rejects_a_live_pid_from_an_older_lock_format(tmp_path: Path) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    paths.tray_pid_file.write_text("111", encoding="ascii")

    assert controller.claim() != 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "111"


def test_contended_claim_cannot_steal_a_live_or_replaced_lock(tmp_path: Path) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    owner = platform_tray.TrayProcessLock(paths.tray_pid_file)
    assert owner.acquire() is True
    owner.write_pid(111)

    assert controller.claim() != 0
    paths.tray_pid_file.write_text("777", encoding="ascii")
    assert controller.claim() != 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "777"

    owner.release()
    assert controller.claim() == 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"
    controller.release()


def test_process_death_releases_the_tray_process_lock(tmp_path: Path) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    script = (
        "from pathlib import Path; import os, sys, time; "
        "from universal_search.platforms.tray import TrayProcessLock; "
        "lock = TrayProcessLock(Path(sys.argv[1])); "
        "assert lock.acquire(); lock.write_pid(os.getpid()); "
        "print('ready', flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(paths.tray_pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert controller.claim() != 0
        process.terminate()
        process.wait(timeout=5)
        assert controller.claim() == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    controller.release()


def test_claim_failure_is_nonzero_and_does_not_create_lock(
    tmp_path: Path,
) -> None:
    class FailingLock:
        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            return None

    controller, _service, _backend, paths = make_controller(
        tmp_path,
        lock_factory=lambda _path: FailingLock(),
    )

    assert controller.claim() != 0
    assert not paths.tray_pid_file.exists()


def test_release_is_idempotent_and_never_touches_gui_lock(tmp_path: Path) -> None:
    controller, _service, _backend, paths = make_controller(tmp_path)
    paths.gui_pid_file.write_text("991", encoding="ascii")
    assert controller.claim() == 0

    controller.release()
    controller.release()

    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"
    assert paths.tray_pid_file.with_name("tray.pid.lock").exists()
    assert paths.gui_pid_file.read_text(encoding="ascii") == "991"


def test_exit_stops_only_worker_started_by_this_tray_and_is_idempotent(
    tmp_path: Path,
) -> None:
    controller, service, backend, paths = make_controller(tmp_path, state="stopped")
    paths.gui_pid_file.write_text("991", encoding="ascii")
    assert controller.claim() == 0
    assert controller.run_command(tray.COMMAND_START).ok is True

    first = controller.exit()
    second = controller.exit()

    assert first is second
    assert first.ok is True
    assert service.calls == ["start", "status", "status", "stop"]
    assert backend.exit_requests == 1
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"
    assert paths.gui_pid_file.read_text(encoding="ascii") == "991"


def test_exit_leaves_externally_started_worker_running(tmp_path: Path) -> None:
    controller, service, backend, paths = make_controller(tmp_path)
    assert controller.claim() == 0

    first = controller.exit()
    second = controller.exit()

    assert first is second
    assert first.ok is True
    assert service.calls == []
    assert backend.exit_requests == 1
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_exit_leaves_replacement_worker_running_after_owned_pid_disappears(
    tmp_path: Path,
) -> None:
    controller, service, backend, paths = make_controller(tmp_path, state="stopped")
    assert controller.claim() == 0
    service.start_pid = 5000
    assert controller.run_command(tray.COMMAND_START).ok is True
    service.status_value = BackgroundStatus(state="indexing", pid=6000)

    assert controller.refresh().pid == 6000
    result = controller.exit()

    assert result.ok is True
    assert service.calls == ["start", "status", "status"]
    assert backend.exit_requests == 1
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_stop_command_does_not_stop_replacement_for_owned_pid(tmp_path: Path) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path, state="stopped")
    service.start_pid = 5000
    assert controller.run_command(tray.COMMAND_START).ok is True
    service.status_value = BackgroundStatus(state="indexing", pid=6000)

    result = controller.run_command(tray.COMMAND_STOP)

    assert result.ok is False
    assert service.calls == ["start", "status", "status"]


def test_same_pid_new_generation_is_a_replacement_for_tray_stop(
    tmp_path: Path,
) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path, state="stopped")
    service.start_pid = 5000
    assert controller.run_command(tray.COMMAND_START).ok is True
    original_generation = service.start_generation
    service.status_value = BackgroundStatus(
        state="indexing",
        pid=5000,
        generation="replacement-generation",
        roots=2,
    )

    result = controller.run_command(tray.COMMAND_STOP)

    assert result.ok is False
    assert original_generation != "replacement-generation"
    assert service.stop_expected_owners == []


def test_exit_passes_owned_pid_to_stop_when_replacement_appears(
    tmp_path: Path,
) -> None:
    controller, service, backend, _paths = make_controller(tmp_path, state="stopped")
    service.start_pid = 5000
    service.replacement_pid = 6000
    assert controller.run_command(tray.COMMAND_START).ok is True
    owned_generation = service.start_generation

    result = controller.exit()

    assert result.ok is True
    assert service.stop_expected_owners == [(5000, owned_generation)]
    assert backend.exit_requests == 1


def test_external_stop_keeps_unscoped_service_contract(tmp_path: Path) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path)

    result = controller.run_command(tray.COMMAND_STOP)

    assert result.ok is True
    assert service.stop_expected_owners == [(None, None)]


def test_start_without_verified_pid_is_unsuccessful_and_does_not_stop_worker(
    tmp_path: Path,
) -> None:
    controller, service, backend, _paths = make_controller(tmp_path, state="stopped")
    service.start_pid = None

    result = controller.run_command(tray.COMMAND_START)

    assert result.ok is False
    assert service.stop_expected_owners == []
    assert backend.exit_requests == 0
    assert controller.exit().ok is True
    assert service.stop_expected_owners == []


def test_start_status_failure_is_unsuccessful_and_does_not_claim_startup(
    tmp_path: Path,
) -> None:
    controller, service, backend, _paths = make_controller(tmp_path, state="stopped")
    service.status_error = OSError("status unavailable")

    result = controller.run_command(tray.COMMAND_START)

    assert result.ok is False
    assert service.stop_expected_owners == []
    assert backend.exit_requests == 0
    assert controller.exit().ok is True
    assert service.stop_expected_owners == []


def test_failed_stop_result_keeps_ownership_and_allows_retry(tmp_path: Path) -> None:
    controller, service, backend, paths = make_controller(tmp_path, state="stopped")
    service.stop_result = ("failed", "no se pudo detener")
    assert controller.claim() == 0
    assert controller.run_command(tray.COMMAND_START).ok is True

    first = controller.exit()
    second = controller.exit()

    assert first.ok is False
    assert second.ok is False
    assert service.calls == ["start", "status", "status", "stop", "status", "stop"]
    assert backend.exit_requests == 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_raised_stop_error_keeps_ownership_and_allows_retry(tmp_path: Path) -> None:
    controller, service, backend, paths = make_controller(tmp_path, state="stopped")
    service.stop_error = RuntimeError("stop failed")
    assert controller.claim() == 0
    assert controller.run_command(tray.COMMAND_START).ok is True

    first = controller.exit()
    second = controller.exit()

    assert first.ok is False
    assert second.ok is False
    assert service.calls == ["start", "status", "status", "stop", "status", "stop"]
    assert backend.exit_requests == 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_explicit_stop_suppresses_synchronous_disappearance_notification(
    tmp_path: Path, monkeypatch
) -> None:
    controller, _service, backend, _paths = make_controller(tmp_path)
    observed_user_stop: list[bool] = []

    def recording_policy(previous, current, *, user_requested_stop=False):
        observed_user_stop.append(user_requested_stop)
        return should_notify(
            previous,
            current,
            user_requested_stop=user_requested_stop,
        )

    monkeypatch.setattr(tray, "should_notify", recording_policy)
    controller.refresh()
    assert controller.run_command(tray.COMMAND_STOP).ok is True

    status = controller.refresh()

    assert status.state == "stopped"
    assert observed_user_stop == [False, True]
    assert backend.notifications == []


def test_refresh_notifies_for_unrequested_disappearance_and_updates_tooltip(
    tmp_path: Path,
) -> None:
    controller, service, backend, _paths = make_controller(tmp_path)
    first = controller.refresh()
    assert backend.tooltips[-1] == first.summary
    service.status_value = BackgroundStatus(state="stopped")

    controller.refresh()

    assert backend.notifications[-1] == (
        "Universal Search",
        "el indexador ha desaparecido",
    )


def test_refresh_notifies_after_a_completed_long_indexing_pass(
    tmp_path: Path,
) -> None:
    controller, service, backend, _paths = make_controller(tmp_path)
    started = datetime(2026, 9, 23, 20, 0)
    service.status_value = BackgroundStatus(
        state="indexing",
        pid=4242,
        generation="external-generation",
        updated_at=started.isoformat(),
    )
    controller.refresh()
    service.status_value = BackgroundStatus(
        state="idle",
        pid=4242,
        generation="external-generation",
        updated_at=(started + timedelta(seconds=120)).isoformat(),
    )

    controller.refresh()

    assert backend.notifications[-1] == (
        "Universal Search",
        "Indexación completada",
    )


def test_refresh_never_raises_when_status_and_backend_fail(
    tmp_path: Path, caplog
) -> None:
    backend = FakeBackend(
        notify_error=RuntimeError("notification failed"),
        update_error=RuntimeError("tooltip failed"),
    )
    controller, service, _backend, _paths = make_controller(
        tmp_path, backend=backend
    )
    secret = "DOCUMENT-CONTENT-MUST-NOT-LEAK"
    service.status_error = OSError(secret)

    status = controller.refresh()

    assert status.state == "error"
    assert "OSError" in (status.error or "")
    assert secret not in (status.error or "")
    assert secret not in caplog.text


def test_run_tray_releases_claim_when_notification_area_is_unavailable(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)
    backend = FakeBackend(available=False)

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result != 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"
    assert service.calls == []


def test_run_tray_uses_controller_refresh_tick_and_exit_releases_resources(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)
    backend = FakeBackend(on_run=lambda selected: selected.tick())

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result == 0
    assert service.calls == ["status", "status"]
    assert backend.tooltips[-1] == "Indexando (2 carpeta(s))"
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_startup_notification_is_evaluated_only_after_icon_is_ready(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths, state="error")
    service.status_value = BackgroundStatus(
        state="error",
        pid=4242,
        generation="external-generation",
        error="startup failure",
    )

    class ReadyAwareBackend(FakeBackend):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.ready = False

        def notify(self, title: str, message: str) -> bool:
            if not self.ready:
                return False
            return super().notify(title, message)

        def run(self, on_command, menu, tooltip, tick=None) -> int:
            self.ready = True
            return super().run(on_command, menu, tooltip, tick)

    backend = ReadyAwareBackend(
        on_run=lambda selected: (selected.tick(), selected.tick())
    )

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result == 0
    assert backend.notifications == [
        ("Universal Search", "startup failure")
    ]


def test_run_tray_exit_command_returns_result_and_releases_lock(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)

    def dispatch_exit(selected: FakeBackend) -> None:
        try:
            result = selected.on_command(tray.COMMAND_EXIT)
        except Exception as exc:
            pytest.fail(f"exit callback raised: {exc}")
        assert isinstance(result, tray.CommandResult)

    backend = FakeBackend(on_run=dispatch_exit)

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result == 0
    assert service.calls == ["status"]
    assert backend.exit_requests == 1
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_run_tray_returns_nonzero_and_releases_claim_after_backend_error(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)
    backend = FakeBackend(run_error=RuntimeError("message loop failed"))

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result != 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_run_tray_backend_failure_does_not_stop_owned_worker(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths, state="stopped")

    def start_then_fail(selected: FakeBackend) -> None:
        selected.on_command(tray.COMMAND_START)

    backend = FakeBackend(
        on_run=start_then_fail,
        run_error=RuntimeError("message loop failed"),
    )

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result != 0
    assert service.calls == ["status", "start", "status"]
    assert backend.exit_requests == 0
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"


def test_run_tray_selects_windows_backend_only_on_windows(
    tmp_path: Path, monkeypatch
) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)
    selected: list[tuple[str | None, FakeBackend]] = []
    backend = FakeBackend()

    def windows_factory(platform: str | None = None):
        selected.append((platform, backend))
        return backend

    monkeypatch.setattr(tray, "WindowsTray", windows_factory)
    monkeypatch.setattr(
        tray,
        "NullTray",
        lambda: pytest.fail("non-Windows platform selected NullTray on win32"),
    )

    result = tray.run_tray(
        service=service,
        paths=paths,
        platform="win32",
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result == 0
    assert selected == [("win32", backend)]


def test_failed_non_exit_command_is_bounded_logged_and_kept_as_last_result(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    controller, service, _backend, _paths = make_controller(tmp_path)
    secret = "DOCUMENT-CONTENT-MUST-NOT-LEAK"

    def fail_pause():
        raise RuntimeError(secret * 100)

    monkeypatch.setattr(service, "pause", fail_pause)

    result = controller.run_command(tray.COMMAND_PAUSE)

    assert result.ok is False
    assert controller.last_command_result is result
    assert len(result.message) <= tray.MAX_COMMAND_MESSAGE
    assert secret not in result.message
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_keyboard_interrupt_exits_with_cleanup_code(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    service = FakeService(paths)
    backend = FakeBackend(run_error=KeyboardInterrupt())

    result = tray.run_tray(
        service=service,
        backend=backend,
        paths=paths,
        pid=4242,
        process_alive=lambda _pid: False,
    )

    assert result == 130
    assert paths.tray_pid_file.read_text(encoding="ascii") == "4242"
