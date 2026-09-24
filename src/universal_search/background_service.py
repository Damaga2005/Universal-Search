"""Application-level state and actions for the background indexer.

The worker publishes small coordination files, but those files are not a
state machine by themselves: a lock can be stale, a pause marker can outlive
its worker, and a status write can be missing or corrupt.  This module turns
those observations into one immutable, platform-independent snapshot for the
tray and any other front end.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from universal_search import background
from universal_search.appconfig import AppPaths


STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_INDEXING = "indexing"
STATE_PAUSED = "paused"
STATE_IDLE = "idle"
STATE_ERROR = "error"
STATE_STOPPING = "stopping"

STATES: tuple[str, ...] = (
    STATE_STOPPED,
    STATE_STARTING,
    STATE_INDEXING,
    STATE_PAUSED,
    STATE_IDLE,
    STATE_ERROR,
    STATE_STOPPING,
)

# The marker, not a stale status payload, decides whether a live worker is
# paused.  These are the only worker states that can be reported directly.
_WORKER_STATES = frozenset({STATE_INDEXING, STATE_IDLE, STATE_ERROR})
_NOTIFICATION_DISAPPEARED_STATES = frozenset({STATE_INDEXING, STATE_STARTING})


@dataclass(frozen=True, slots=True)
class BackgroundStatus:
    """One coherent, read-only view of the indexer."""

    state: str
    pid: int | None = None
    generation: str | None = None
    updated_at: str | None = None
    last_scan_at: str | None = None
    last_scan_stats: dict[str, int] | None = None
    roots: int = 0
    pending: str = ""
    error: str | None = None
    hotkey_error: str | None = None
    stale_lock: bool = False
    paused: bool = False
    autostart: bool = False
    problem: str | None = None

    @property
    def running(self) -> bool:
        return self.state not in {STATE_STOPPED, STATE_STOPPING}

    @property
    def can_start(self) -> bool:
        return self.state == STATE_STOPPED

    @property
    def can_stop(self) -> bool:
        return self.state not in {STATE_STOPPED, STATE_STOPPING}

    @property
    def can_pause(self) -> bool:
        return self.state in {STATE_STARTING, STATE_INDEXING, STATE_IDLE}

    @property
    def can_resume(self) -> bool:
        return self.state == STATE_PAUSED

    @property
    def summary(self) -> str:
        """A short, safe description suitable for a tray tooltip or menu."""
        if self.state == STATE_STOPPED:
            if self.stale_lock:
                return "Indexador detenido (bloqueo obsoleto de un proceso muerto)"
            return "Indexador detenido"
        if self.state == STATE_STARTING:
            return "Indexador arrancando..."
        if self.state == STATE_STOPPING:
            return "Indexador terminando..."
        if self.state == STATE_INDEXING:
            return f"Indexando ({self.roots} carpeta(s))"
        if self.state == STATE_PAUSED:
            return "Indexación en pausa"
        if self.state == STATE_ERROR:
            return f"Error del indexador: {self.error or 'desconocido'}"
        return f"Indexador inactivo ({self.roots} carpeta(s))"

    def as_dict(self) -> dict[str, Any]:
        """Return a serializable snapshot for diagnostics and the tray."""
        return {
            "state": self.state,
            "pid": self.pid,
            "generation": self.generation,
            "updated_at": self.updated_at,
            "last_scan_at": self.last_scan_at,
            "last_scan_stats": self.last_scan_stats,
            "roots": self.roots,
            "pending": self.pending,
            "error": self.error,
            "hotkey_error": self.hotkey_error,
            "stale_lock": self.stale_lock,
            "paused": self.paused,
            "autostart": self.autostart,
            "problem": self.problem,
            "summary": self.summary,
        }


def _pending_note(state: str, paused: bool) -> str:
    """Describe work by mode; the indexer has no queue-depth counter."""
    if state == STATE_STOPPED:
        return "sin work: el índice no se está actualizando"
    if state == STATE_STARTING:
        return "pendiente: la primera pasada aún no ha empezado"
    if state == STATE_STOPPING:
        return "pendiente: terminar la pasada en curso"
    if state == STATE_PAUSED or paused:
        return "en pausa: los cambios de disco esperan"
    if state == STATE_INDEXING:
        return "pasada en curso"
    if state == STATE_ERROR:
        return "bloqueado hasta resolver el error"
    return "sin trabajo pendiente conocido"


def _safe_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _safe_generation(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    generation = value.strip()
    return generation if generation and len(generation) <= 128 else None


def _safe_roots(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_stats(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key, count in value.items():
        if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool):
            result[key] = count
    return result


def _problem_text(problem: str | None, message: str) -> str:
    if not problem:
        return message
    if message in problem:
        return problem
    return f"{problem}; {message}"


class BackgroundService:
    """Read the worker state and delegate lifecycle operations to it."""

    def __init__(self, paths: AppPaths | None = None, registry=None) -> None:
        self.paths = paths or AppPaths.discover()
        self.registry = registry

    def status(self) -> BackgroundStatus:
        """Return a defensive snapshot; coordination failures do not escape."""
        problem: str | None = None
        payload: dict[str, Any] = {}
        status_payload_valid = False
        try:
            raw_status = background.read_status(self.paths)
            if isinstance(raw_status, dict):
                payload = raw_status
                status_payload_valid = True
        except Exception as exc:
            problem = f"estado ilegible ({type(exc).__name__})"
        try:
            # read_status intentionally returns None for both a missing file
            # and an invalid payload.  A present file with no usable payload
            # is still worth reporting to the user.
            status_file_present = self.paths.status_file.exists()
        except OSError as exc:
            status_file_present = False
            problem = _problem_text(
                problem, f"no se pudo comprobar el estado ({type(exc).__name__})"
            )
        if not status_payload_valid and status_file_present:
            problem = _problem_text(problem, "estado ilegible")

        lock_pid: int | None = None
        lock_payload_valid = False
        try:
            raw_pid = background.read_lock_pid(self.paths)
            if raw_pid is not None:
                lock_payload_valid = True
                # A malformed value is equivalent to no usable lock, but it
                # should not make a status poll fail.
                try:
                    lock_pid = int(raw_pid)
                except (TypeError, ValueError, OverflowError):
                    problem = _problem_text(problem, "PID de bloqueo ilegible")
        except Exception as exc:
            problem = _problem_text(problem, f"bloqueo ilegible ({type(exc).__name__})")
        try:
            lock_file_present = self.paths.lock_file.exists()
        except OSError as exc:
            lock_file_present = False
            problem = _problem_text(
                problem, f"no se pudo comprobar el bloqueo ({type(exc).__name__})"
            )
        if not lock_payload_valid and lock_file_present:
            problem = _problem_text(problem, "bloqueo ilegible")

        lock_owner = None
        owner_identity_valid = lock_pid is None
        if lock_pid is not None:
            try:
                recorded_owner = background.read_worker_owner(self.paths)
                owner_file_present = self.paths.worker_owner_file.exists()
            except Exception as exc:
                recorded_owner = None
                owner_file_present = True
                problem = _problem_text(
                    problem,
                    f"identidad del trabajador ilegible ({type(exc).__name__})",
                )
            if owner_file_present and (
                recorded_owner is None or recorded_owner.pid != lock_pid
            ):
                owner_identity_valid = False
                problem = _problem_text(problem, "identidad del trabajador ilegible")
            else:
                # Missing owner sidecars are the phase-006 PID-only format.
                lock_owner = recorded_owner or background.WorkerOwner(lock_pid, None)
                owner_identity_valid = True

        alive = False
        if lock_pid is not None:
            try:
                alive = bool(background.process_alive(lock_pid))
            except Exception as exc:
                problem = _problem_text(
                    problem, f"no se pudo comprobar el proceso ({type(exc).__name__})"
                )

        paused = False
        try:
            paused = bool(background.is_paused(self.paths))
        except Exception as exc:
            problem = _problem_text(problem, f"no se pudo leer la pausa ({type(exc).__name__})")

        stopping = False
        try:
            stopping = bool(
                alive
                and owner_identity_valid
                and background.stop_requested(self.paths, owner=lock_owner)
            )
        except Exception as exc:
            problem = _problem_text(problem, f"no se pudo leer la parada ({type(exc).__name__})")

        status_pid = payload.get("pid")
        status_generation = _safe_generation(payload.get("generation"))
        lock_generation = lock_owner.generation if lock_owner is not None else None
        status_belongs_to_live_worker = (
            alive
            and owner_identity_valid
            and isinstance(status_pid, int)
            and not isinstance(status_pid, bool)
            and status_pid == lock_pid
            and status_generation == lock_generation
        )
        current_payload = payload if status_belongs_to_live_worker else {}
        worker_state = _safe_text(current_payload.get("state")) or ""
        if stopping and alive:
            state = STATE_STOPPING
        elif not alive:
            state = STATE_STOPPED
        elif paused:
            state = STATE_PAUSED
        elif worker_state in _WORKER_STATES:
            state = worker_state
        else:
            state = STATE_STARTING

        stale_lock = lock_pid is not None and not alive
        if stale_lock:
            problem = _problem_text(
                problem,
                f"el proceso {lock_pid} ya no existe; el bloqueo se limpiará al arrancar de nuevo",
            )

        autostart = False
        try:
            autostart = bool(background.get_autostart(registry=self.registry))
        except Exception:
            # Autostart is a Windows integration detail.  A missing registry
            # module or denied key must not make a tray/status poll fail.
            autostart = False

        last_scan_stats = _safe_stats(payload.get("last_scan_stats"))
        return BackgroundStatus(
            state=state,
            pid=lock_pid if alive else None,
            generation=(
                _safe_generation(current_payload.get("generation"))
                if status_belongs_to_live_worker
                else None
            ),
            updated_at=_safe_text(current_payload.get("updated_at")),
            last_scan_at=_safe_text(payload.get("last_scan_at")),
            last_scan_stats=last_scan_stats,
            roots=_safe_roots(current_payload.get("roots")),
            pending=_pending_note(state, paused),
            error=_safe_text(current_payload.get("error")),
            hotkey_error=_safe_text(current_payload.get("hotkey")),
            stale_lock=stale_lock,
            paused=paused,
            autostart=autostart,
            problem=problem,
        )

    # -- actions ------------------------------------------------------------------

    def start(self, generation: str | None = None) -> tuple[str, str]:
        return background.start(self.paths, generation=generation)

    def stop(
        self,
        *,
        timeout: float = 8.0,
        expected_pid: int | None = None,
        expected_generation: str | None = None,
    ) -> tuple[str, str]:
        if expected_pid is None and expected_generation is None:
            return background.stop(self.paths, timeout=timeout)
        return background.stop(
            self.paths,
            timeout=timeout,
            expected_pid=expected_pid,
            expected_generation=expected_generation,
        )

    def pause(self) -> tuple[str, str]:
        status = self.status()
        if status.state == STATE_STOPPED:
            return "not-running", "el indexador no está en marcha"
        if not status.can_pause:
            return "unavailable", f"no se puede pausar en estado {status.state}"
        background.pause(self.paths)
        return "paused", "indexación en pausa"

    def resume(self) -> tuple[str, str]:
        status = self.status()
        if status.state == STATE_STOPPED:
            return "not-running", "el indexador no está en marcha"
        if not status.can_resume:
            return "unavailable", f"no se puede reanudar en estado {status.state}"
        background.resume(self.paths)
        return "resumed", "indexación reanudada"

    def autostart_enabled(self) -> bool:
        try:
            return bool(background.get_autostart(registry=self.registry))
        except Exception:
            return False


# -- notification policy ---------------------------------------------------------

LONG_RUN_SECONDS = 120


def should_notify(
    previous: BackgroundStatus | None,
    current: BackgroundStatus,
    *,
    user_requested_stop: bool = False,
) -> str | None:
    """Return an interruption reason only for the approved transitions.

    ``user_requested_stop`` is provenance supplied by the controller when it
    has just issued an explicit stop command.  It suppresses a disappearance
    notification for the resulting direct ``indexing``/``starting`` to
    ``stopped`` transition; callers that do not know the origin keep the
    default crash-detection behavior.
    """
    if current.state == STATE_ERROR:
        if (
            previous is None
            or previous.state != STATE_ERROR
            or previous.error != current.error
            or (
                previous.generation != current.generation
                and (previous.generation is not None or current.generation is not None)
            )
        ):
            return current.error or "el indexador ha fallado"

    if current.hotkey_error and (
        previous is None
        or previous.hotkey_error != current.hotkey_error
        or (
            previous.generation != current.generation
            and (previous.generation is not None or current.generation is not None)
        )
    ):
        return current.hotkey_error

    if (
        current.state == STATE_STOPPED
        and previous is not None
        and previous.state in _NOTIFICATION_DISAPPEARED_STATES
    ):
        if user_requested_stop:
            return None
        if current.stale_lock:
            return "el indexador ha desaparecido; el bloqueo quedó obsoleto"
        return "el indexador ha desaparecido"
    return None


def run_completed_long_ago(
    previous: BackgroundStatus | None,
    current: BackgroundStatus,
    *,
    long_run_seconds: float = LONG_RUN_SECONDS,
) -> bool:
    """Whether indexing transitioned to idle after a sufficiently long pass."""
    if previous is None or current.state != STATE_IDLE:
        return False
    if previous.state != STATE_INDEXING:
        return False
    if (
        previous.generation is None
        or current.generation is None
        or previous.generation != current.generation
    ):
        return False
    if not previous.updated_at or not current.updated_at:
        return False
    try:
        started = datetime.fromisoformat(previous.updated_at)
        ended = datetime.fromisoformat(current.updated_at)
        elapsed = (ended - started).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return False
    return elapsed >= long_run_seconds


__all__ = [
    "LONG_RUN_SECONDS",
    "STATES",
    "STATE_ERROR",
    "STATE_IDLE",
    "STATE_INDEXING",
    "STATE_PAUSED",
    "STATE_STARTING",
    "STATE_STOPPED",
    "STATE_STOPPING",
    "BackgroundService",
    "BackgroundStatus",
    "run_completed_long_ago",
    "should_notify",
]
