"""Platform-independent coordination for the notification-area controller."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from universal_search import background
from universal_search.appconfig import AppPaths
from universal_search.background_service import (
    STATE_ERROR,
    STATE_STOPPED,
    BackgroundService,
    BackgroundStatus,
    run_completed_long_ago,
    should_notify,
)
from universal_search.hotkey import launch_gui, request_diagnostics, request_show
from universal_search.platforms.tray import (
    MenuItem,
    NullTray,
    TrayBackend,
    TrayProcessLock,
    WindowsTray,
)


log = logging.getLogger("universal_search.tray")

# Keep command one aligned with the backend's double-click activation command.
COMMAND_OPEN = 1
COMMAND_QUICK_SEARCH = 2
COMMAND_PAUSE = 3
COMMAND_RESUME = 4
COMMAND_START = 5
COMMAND_STOP = 6
COMMAND_DIAGNOSTICS = 7
COMMAND_SETTINGS = 8
COMMAND_EXIT = 9

CLAIM_SUCCEEDED = 0
CLAIM_UNAVAILABLE = 1
CLAIM_FAILED = 2
MAX_COMMAND_MESSAGE = 300


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of one user command, safe to ignore in the native loop."""

    ok: bool
    message: str


def build_menu(status: BackgroundStatus) -> list[MenuItem]:
    """Build the state-specific menu without touching platform APIs."""

    items = [
        MenuItem(0, status.summary, enabled=False),
        MenuItem(COMMAND_OPEN, "Abrir Universal Search"),
        MenuItem(COMMAND_QUICK_SEARCH, "Búsqueda rápida"),
    ]
    if status.can_resume:
        items.append(MenuItem(COMMAND_RESUME, "Reanudar indexación"))
    elif status.can_pause:
        items.append(MenuItem(COMMAND_PAUSE, "Pausar indexación"))

    if status.can_start:
        items.append(MenuItem(COMMAND_START, "Iniciar indexador"))
    elif status.can_stop:
        items.append(MenuItem(COMMAND_STOP, "Detener indexador"))

    items.extend(
        [
            MenuItem(COMMAND_DIAGNOSTICS, "Diagnóstico"),
            MenuItem(COMMAND_SETTINGS, "Configuración"),
            MenuItem(0, "", separator=True),
            MenuItem(COMMAND_EXIT, "Salir"),
        ]
    )
    return items


class TrayController:
    """Own tray state and translate backend commands into application actions."""

    def __init__(
        self,
        service: BackgroundService,
        backend: TrayBackend,
        paths: AppPaths | None = None,
        *,
        pid: int | None = None,
        process_alive: Callable[[int], bool] | None = None,
        lock_factory: Callable[[Path], object] | None = None,
    ) -> None:
        self.service = service
        self.backend = backend
        self.paths = paths or service.paths
        self._pid = os.getpid() if pid is None else int(pid)
        self._process_alive = process_alive or background.process_alive
        lock_class = lock_factory or TrayProcessLock
        self._lock = lock_class(self.paths.tray_pid_file)
        self._claimed = False
        self._owned_worker_pid: int | None = None
        self._owned_worker_generation: str | None = None
        self._user_requested_stop = False
        self._status: BackgroundStatus | None = None
        self._exit_result: CommandResult | None = None
        self.last_command_result: CommandResult | None = None

    def claim(self) -> int:
        """Claim the held OS process lock and retain the current PID."""

        if self._claimed:
            return CLAIM_SUCCEEDED
        try:
            self.paths.ensure()
        except OSError:
            log.exception("could not prepare the tray application home")
            return CLAIM_FAILED

        try:
            acquired = self._lock.acquire()
        except Exception:
            log.exception("could not acquire the tray process lock")
            return CLAIM_FAILED
        if not acquired:
            return CLAIM_UNAVAILABLE

        try:
            previous_pid = self._lock.read_pid()
            if previous_pid is not None and previous_pid != self._pid:
                try:
                    previous_alive = self._process_alive(previous_pid)
                except Exception:
                    log.exception("could not check the previous tray PID")
                    self._lock.release()
                    return CLAIM_UNAVAILABLE
                if previous_alive:
                    self._lock.release()
                    return CLAIM_UNAVAILABLE
            if previous_pid is not None:
                log.debug("replacing unlocked tray PID %s", previous_pid)
            self._lock.write_pid(self._pid)
        except Exception:
            try:
                self._lock.release()
            except Exception:
                log.exception("could not roll back the tray process lock")
            log.exception("could not write the tray PID lock")
            return CLAIM_FAILED
        self._claimed = True
        return CLAIM_SUCCEEDED

    def release(self) -> None:
        """Release this controller's process lock without touching GUI state."""

        if not self._claimed:
            return
        self._claimed = False
        try:
            self._lock.release()
        except Exception:
            log.exception("could not release the tray process lock")

    def initialize(self) -> BackgroundStatus:
        """Capture the transition baseline without notifying before icon readiness."""

        try:
            current = self.service.status()
        except Exception as exc:
            log.warning("could not read initial tray status (%s)", type(exc).__name__)
            current = BackgroundStatus(
                state=STATE_ERROR,
                error="no se pudo leer el estado",
            )
        self._reconcile_owned_worker(current)
        # Leave ``_status`` unchanged: the first post-icon refresh must see
        # this startup condition as a transition, not as already consumed.
        return current

    def refresh(self) -> BackgroundStatus:
        """Refresh status, notifications, and tooltip without escaping errors."""

        previous = self._status
        try:
            current = self.service.status()
        except Exception as exc:
            log.warning("could not read tray status (%s)", type(exc).__name__)
            current = BackgroundStatus(
                state=STATE_ERROR,
                error=f"no se pudo leer el estado: {type(exc).__name__}",
            )
        else:
            self._reconcile_owned_worker(current)

        reason: str | None = None
        try:
            reason = should_notify(
                previous,
                current,
                user_requested_stop=self._user_requested_stop,
            )
            if reason is None and run_completed_long_ago(previous, current):
                reason = "Indexación completada"
        except Exception:
            log.exception("could not evaluate the tray notification policy")

        if reason:
            try:
                self.backend.notify("Universal Search", reason)
            except Exception as exc:
                log.warning(
                    "could not show a tray notification (%s)",
                    type(exc).__name__,
                )
        try:
            self.backend.update_tooltip(current.summary)
        except Exception as exc:
            log.warning(
                "could not update the tray tooltip (%s)",
                type(exc).__name__,
            )

        self._status = current
        if self._user_requested_stop and current.state == STATE_STOPPED:
            self._user_requested_stop = False
        return current

    def menu(self) -> list[MenuItem]:
        """Return a menu from a fresh application-state snapshot."""

        return build_menu(self.refresh())

    def run_command(self, command: int) -> CommandResult:
        """Dispatch one command, retain its result, and log bounded failures."""

        if command == COMMAND_OPEN:
            result = self._activate_gui()
        elif command in {COMMAND_QUICK_SEARCH, COMMAND_SETTINGS}:
            result = self._activate_gui()
        elif command == COMMAND_DIAGNOSTICS:
            result = self._open_diagnostics()
        elif command == COMMAND_PAUSE:
            result = self._pause()
        elif command == COMMAND_RESUME:
            result = self._resume()
        elif command == COMMAND_START:
            result = self._start()
        elif command == COMMAND_STOP:
            result = self._stop()
        elif command == COMMAND_EXIT:
            result = self.exit()
        else:
            result = CommandResult(False, f"Comando desconocido: {command}")
        if len(result.message) > MAX_COMMAND_MESSAGE:
            result = CommandResult(
                result.ok,
                result.message[: MAX_COMMAND_MESSAGE - 3] + "...",
            )
        self.last_command_result = result
        if not result.ok and command != COMMAND_EXIT:
            log.warning("tray command %s failed: %s", command, result.message)
        return result

    def exit(self) -> CommandResult:
        """Stop only the exact owned worker, then request native exit."""

        if self._exit_result is not None:
            return self._exit_result

        if self._owned_worker_pid is not None:
            try:
                current = self.service.status()
            except Exception as exc:
                log.warning(
                    "could not verify the tray-owned worker (%s)",
                    type(exc).__name__,
                )
                return CommandResult(
                    False,
                    f"No se pudo comprobar el indexador: {type(exc).__name__}",
                )
            if self._owns_status(current):
                try:
                    code, message = self.service.stop(
                        expected_pid=self._owned_worker_pid,
                        expected_generation=self._owned_worker_generation,
                    )
                except Exception as exc:
                    log.warning(
                        "could not stop the tray-owned worker (%s)",
                        type(exc).__name__,
                    )
                    return CommandResult(
                        False,
                        f"No se pudo detener el indexador: {type(exc).__name__}",
                    )
                if code == "replaced":
                    self._clear_owned_worker()
                elif code not in {"stopped", "not-running"}:
                    return CommandResult(False, message)
                else:
                    self._clear_owned_worker()
            else:
                self._clear_owned_worker()

        try:
            requested = self.backend.request_exit()
        except Exception as exc:
            log.warning("could not request tray loop exit (%s)", type(exc).__name__)
            return CommandResult(
                False,
                f"No se pudo cerrar la bandeja: {type(exc).__name__}",
            )
        if not requested:
            return CommandResult(False, "No se pudo cerrar la bandeja")

        self.release()
        result = CommandResult(True, "Bandera cerrada")
        self._exit_result = result
        return result

    def _owns_status(self, status: BackgroundStatus) -> bool:
        return (
            self._owned_worker_pid is not None
            and status.pid == self._owned_worker_pid
            and status.generation == self._owned_worker_generation
        )

    def _clear_owned_worker(self) -> None:
        self._owned_worker_pid = None
        self._owned_worker_generation = None

    def _reconcile_owned_worker(self, status: BackgroundStatus) -> None:
        if self._owned_worker_pid is not None and not self._owns_status(status):
            self._clear_owned_worker()

    def _activate_gui(self) -> CommandResult:
        try:
            if request_show(self.paths):
                return CommandResult(True, "Universal Search abierto")
        except Exception as exc:
            log.warning(
                "could not signal the existing search window (%s)",
                type(exc).__name__,
            )
        try:
            launch_gui()
        except Exception as exc:
            return CommandResult(
                False,
                f"No se pudo abrir Universal Search: {type(exc).__name__}",
            )
        return CommandResult(True, "Universal Search se está abriendo")

    def _open_diagnostics(self) -> CommandResult:
        try:
            if request_diagnostics(self.paths):
                return CommandResult(True, "Diagnóstico solicitado")
        except Exception as exc:
            log.warning(
                "could not signal the diagnostics view (%s)",
                type(exc).__name__,
            )

        try:
            launch_gui()
            self.paths.ensure()
            self.paths.diagnostics_request_file.touch()
        except Exception as exc:
            return CommandResult(
                False,
                f"No se pudo abrir el diagnóstico: {type(exc).__name__}",
            )
        return CommandResult(True, "Diagnóstico se está abriendo")

    def _pause(self) -> CommandResult:
        try:
            code, message = self.service.pause()
        except Exception as exc:
            return CommandResult(
                False,
                f"No se pudo pausar la indexación: {type(exc).__name__}",
            )
        return CommandResult(code == "paused", message)

    def _resume(self) -> CommandResult:
        try:
            code, message = self.service.resume()
        except Exception as exc:
            return CommandResult(
                False,
                f"No se pudo reanudar la indexación: {type(exc).__name__}",
            )
        return CommandResult(code == "resumed", message)

    def _start(self) -> CommandResult:
        generation = background.new_worker_generation()
        try:
            code, message = self.service.start(generation=generation)
        except Exception as exc:
            return CommandResult(
                False,
                f"No se pudo iniciar el indexador: {type(exc).__name__}",
            )
        if code == "started":
            try:
                current = self.service.status()
            except Exception as exc:
                log.warning(
                    "could not identify the tray-started worker (%s)",
                    type(exc).__name__,
                )
                return CommandResult(
                    False,
                    "No se pudo verificar la identidad del indexador iniciado",
                )
            if current.pid is None or current.pid <= 0:
                return CommandResult(
                    False,
                    "El indexador inició sin un PID verificable",
                )
            if current.generation != generation:
                return CommandResult(
                    False,
                    "El indexador iniciado no presentó la generación esperada",
                )
            self._owned_worker_pid = int(current.pid)
            self._owned_worker_generation = generation
        return CommandResult(code in {"started", "already-running"}, message)

    def _stop(self) -> CommandResult:
        if self._owned_worker_pid is not None:
            try:
                current = self.service.status()
            except Exception as exc:
                return CommandResult(
                    False,
                    "No se pudo comprobar el indexador: "
                    f"{type(exc).__name__}",
                )
            if not self._owns_status(current):
                self._clear_owned_worker()
                return CommandResult(
                    False,
                    "El indexador cambió; no se detuvo el proceso sustituto",
                )

        self._user_requested_stop = True
        expected_pid = self._owned_worker_pid
        expected_generation = self._owned_worker_generation
        try:
            if expected_pid is None:
                code, message = self.service.stop()
            else:
                code, message = self.service.stop(
                    expected_pid=expected_pid,
                    expected_generation=expected_generation,
                )
        except Exception as exc:
            self._user_requested_stop = False
            return CommandResult(
                False,
                f"No se pudo detener el indexador: {type(exc).__name__}",
            )
        if code == "replaced":
            self._clear_owned_worker()
            self._user_requested_stop = False
            return CommandResult(False, message)
        if code in {"stopped", "not-running"}:
            self._clear_owned_worker()
            if code == "not-running":
                self._user_requested_stop = False
        else:
            self._user_requested_stop = False
        return CommandResult(code in {"stopped", "not-running"}, message)



def run_tray(
    service: BackgroundService | None = None,
    backend: TrayBackend | None = None,
    *,
    paths: AppPaths | None = None,
    platform: str | None = None,
    pid: int | None = None,
    process_alive: Callable[[int], bool] | None = None,
) -> int:
    """Run the controller with a platform-safe backend and bounded cleanup."""

    selected_paths = paths
    if selected_paths is None:
        selected_paths = service.paths if service is not None else AppPaths.discover()
    selected_service = service or BackgroundService(selected_paths)
    selected_platform = sys.platform if platform is None else platform
    if backend is None:
        backend = (
            WindowsTray(platform=selected_platform)
            if selected_platform == "win32"
            else NullTray()
        )

    controller = TrayController(
        selected_service,
        backend,
        paths=selected_paths,
        pid=pid,
        process_alive=process_alive,
    )
    claim_result = controller.claim()
    if claim_result != CLAIM_SUCCEEDED:
        log.warning(
            "no se pudo iniciar la bandeja: otra instancia puede estar activa "
            "o el bloqueo no es accesible"
        )
        return claim_result

    try:
        if not backend.available():
            log.error(
                "no hay un área de notificación disponible; "
                "el comando 'tray' no puede mostrar su icono"
            )
            return 1
        status = controller.initialize()
        try:
            result = backend.run(
                controller.run_command,
                controller.menu,
                status.summary,
                tick=controller.refresh,
            )
            return int(result)
        except KeyboardInterrupt:
            log.info("tray interrupted; shutting down safely")
            return 130
        except Exception as exc:
            log.warning(
                "the tray notification-area loop failed (%s)",
                type(exc).__name__,
            )
            return 1
    finally:
        controller.release()


__all__ = [
    "COMMAND_DIAGNOSTICS",
    "COMMAND_EXIT",
    "COMMAND_OPEN",
    "COMMAND_PAUSE",
    "COMMAND_QUICK_SEARCH",
    "COMMAND_RESUME",
    "COMMAND_SETTINGS",
    "COMMAND_START",
    "COMMAND_STOP",
    "CommandResult",
    "TrayController",
    "build_menu",
    "run_tray",
]
