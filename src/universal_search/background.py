"""Background indexer: single-instance lifecycle, status, pause/stop, autostart.

The worker runs as a separate process (``universal-search indexer run``) so
closing the GUI never stops indexing. Coordination with the outside world
happens exclusively through files in the per-user application directory:

    indexer.lock                          PID of the running worker (legacy-visible contract)
    indexer.lock.lease                    OS-held exclusive lease for stale-lock recovery
    indexer.lock.owner                    PID + generation + process creation identity
    indexer.starting.<pid>.<hash>.claim   PID-scoped atomic starter claim
    indexer.starting.lease                OS-held startup-claim recovery lease
    indexer-status.json                   atomically written state + owner generation
    indexer-paused.flag                   presence == paused
    indexer-stop.<hash>.flag              one generation's graceful stop request
    indexer-stop.flag                     legacy generationless stop request

Crash-safe writes come from the database layer (SQLite WAL + one transaction
per file); status JSON writes are atomic via ``os.replace``.

Responsiveness comes from filesystem notifications (watchdog), but periodic
reconciliation remains the correctness mechanism: notifications are only an
optimization and may be missed.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.context import configured_roots
from universal_search.hotkey import HotkeyServer, launch_gui, request_show
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import IndexStats, Indexer
from universal_search.metrics import set_sink
from universal_search.platforms.worker import (
    FileLease,
    ProcessIdentity,
    current_process_creation_id,
    open_process_identity,
    terminate_process_identity,
)

log = logging.getLogger("universal_search.indexer")



STATE_IDLE = "idle"
STATE_INDEXING = "indexing"
STATE_PAUSED = "paused"
STATE_ERROR = "error"

EXIT_OK = 0
EXIT_CRASH = 1
EXIT_ALREADY_RUNNING = 2

# Loop granularity: how often the worker re-checks stop/pause markers.
STOP_POLL_SECONDS = 0.25
# Debounce for filesystem events so bursts trigger one pass, not many.
MIN_WATCH_INTERVAL = 1.0
# creation flags for the detached worker process
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class WorkerAlreadyRunning(RuntimeError):
    """Another live worker holds the lock."""


class LockError(RuntimeError):
    """The lock could not be acquired."""


class StartupClaimCleanupError(LockError):
    """An exact startup-claim path remained after bounded cleanup retries."""

    def __init__(self, paths: tuple[Path, ...], cause: OSError) -> None:
        self.paths = paths
        self.cause = cause
        locations = ", ".join(str(path) for path in paths)
        super().__init__(
            "no se pudo limpiar la reclamación de arranque "
            f"({locations}); reintenta la operación o reinicia la aplicación "
            "para liberarla"
        )


WORKER_GENERATION_ENV = "UNIVERSAL_SEARCH_WORKER_GENERATION"
_MAX_GENERATION_CHARS = 128
_STARTUP_CLAIM_UNLINK_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class WorkerOwner:
    """One worker PID and its unique startup generation."""

    pid: int
    generation: str | None
    creation_id: str | None = None

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("worker PID must be positive")
        if self.generation is not None and not self.generation.strip():
            raise ValueError("worker generation must not be blank")
        if self.creation_id is not None and not self.creation_id.strip():
            raise ValueError("worker creation identity must not be blank")


def new_worker_generation() -> str:
    """Return an unpredictable local startup identity."""

    return uuid.uuid4().hex


def _normalise_generation(value: str | None) -> str | None:
    if value is None:
        return None
    generation = str(value).strip()
    if not generation:
        return None
    if len(generation) > _MAX_GENERATION_CHARS:
        raise ValueError("worker generation is too long")
    return generation


def _lease_key(paths: AppPaths) -> str:
    return str(paths.home.absolute())


def _unlink_best_effort(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        # The held OS lease, not deletion of diagnostic files, is authoritative.
        pass


def _read_startup_claim_path(path: Path) -> WorkerOwner | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = payload.get("pid")
        generation = payload.get("generation")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if not isinstance(generation, str) or not generation.strip():
            return None
        return WorkerOwner(pid, _normalise_generation(generation))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def read_startup_claim(paths: AppPaths) -> WorkerOwner | None:
    """Read a valid PID-scoped claim, then the legacy generic claim."""
    for path in sorted(_scoped_startup_claim_paths(paths)):
        claim = _read_startup_claim_path(path)
        if claim is not None:
            return claim
    return _read_startup_claim_path(paths.startup_claim_file)


def _scoped_startup_claim_identity(path: Path) -> tuple[int, str] | None:
    parts = path.name.split(".")
    if len(parts) != 5 or parts[0:2] != ["indexer", "starting"]:
        return None
    if parts[4] not in {"claim", "tmp"} or not parts[2].isdigit():
        return None
    token = parts[3]
    if len(token) != 64 or any(
        character not in "0123456789abcdef" for character in token
    ):
        return None
    pid = int(parts[2])
    return (pid, token) if pid > 0 else None


def _scoped_startup_claim_paths(paths: AppPaths) -> list[Path]:
    return [
        *paths.home.glob("indexer.starting.*.claim"),
        *paths.home.glob("indexer.starting.*.tmp"),
    ]


def _unlink_startup_claim_paths(
    paths: tuple[Path, ...],
) -> None:
    failed: list[tuple[Path, OSError]] = []
    for path in paths:
        last_error: OSError | None = None
        for _attempt in range(_STARTUP_CLAIM_UNLINK_ATTEMPTS):
            try:
                path.unlink()
            except FileNotFoundError:
                last_error = None
                break
            except OSError as exc:
                last_error = exc
            else:
                last_error = None
                break
        if last_error is not None:
            failed.append((path, last_error))
    if failed:
        raise StartupClaimCleanupError(
            tuple(path for path, _error in failed),
            failed[0][1],
        )


def _write_startup_claim(paths: AppPaths, claim: WorkerOwner) -> None:
    if claim.generation is None:
        raise ValueError("startup claims require a worker generation")
    paths.ensure()
    temporary = paths.startup_claim_temporary_file_for(claim.pid, claim.generation)
    final = paths.startup_claim_file_for(claim.pid, claim.generation)
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": claim.pid, "generation": claim.generation}, handle)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, final)
    except BaseException as publication_error:
        try:
            _unlink_startup_claim_paths((final, temporary))
        except StartupClaimCleanupError as cleanup_error:
            raise cleanup_error from publication_error
        raise


def _clear_startup_claim(
    paths: AppPaths,
    generation: str,
    *,
    parent_pid: int | None = None,
) -> None:
    if parent_pid is not None:
        candidates = [
            paths.startup_claim_file_for(parent_pid, generation),
            paths.startup_claim_temporary_file_for(parent_pid, generation),
        ]
    else:
        candidates = _scoped_startup_claim_paths(paths)
    generation_token = AppPaths.generation_filename_token(generation)
    owned_paths: list[Path] = []
    for path in candidates:
        claim = _read_startup_claim_path(path)
        if claim is not None:
            if claim.generation != generation:
                continue
            if parent_pid is not None and claim.pid != parent_pid:
                continue
        else:
            identity = _scoped_startup_claim_identity(path)
            if identity is None or identity[1] != generation_token:
                continue
            if parent_pid is not None and identity[0] != parent_pid:
                continue
        owned_paths.append(path)

    legacy = _read_startup_claim_path(paths.startup_claim_file)
    if (
        legacy is not None
        and legacy.generation == generation
        and (parent_pid is None or legacy.pid == parent_pid)
    ):
        owned_paths.append(paths.startup_claim_file)
    _unlink_startup_claim_paths(tuple(owned_paths))


def _acquire_startup_claim(paths: AppPaths, generation: str) -> tuple[str, str]:
    """Claim one launcher generation while serializing stale-claim recovery."""

    try:
        paths.ensure()
    except OSError as exc:
        log.warning("could not prepare worker startup claim (%s)", type(exc).__name__)
        return "failed", "no se pudo preparar el arranque del indexador"
    lease = FileLease(paths.startup_lease_file)
    if not lease.acquire():
        return "busy", "otro indexador está arrancando"
    try:
        own_claim_exists = False
        recovered = False
        selected_token = AppPaths.generation_filename_token(generation)
        for path in sorted(_scoped_startup_claim_paths(paths)):
            claim = _read_startup_claim_path(path)
            if claim is not None:
                if claim.pid == os.getpid() and claim.generation == generation:
                    own_claim_exists = True
                    continue
                if process_alive(claim.pid):
                    return "busy", "otro indexador está arrancando"
                _unlink_startup_claim_paths((path,))
                recovered = True
                continue

            identity = _scoped_startup_claim_identity(path)
            if identity is None:
                return "failed", "no se pudo reclamar el arranque: identidad ilegible"
            pid, token = identity
            if process_alive(pid):
                if pid == os.getpid() and token == selected_token:
                    _unlink_startup_claim_paths((path,))
                    recovered = True
                else:
                    return "busy", "otro indexador está arrancando"
            else:
                _unlink_startup_claim_paths((path,))
                recovered = True

        legacy_path = paths.startup_claim_file
        if legacy_path.exists():
            legacy = _read_startup_claim_path(paths.startup_claim_file)
            if legacy is None:
                return "failed", "no se pudo reclamar el arranque: identidad ilegible"
            if legacy.pid == os.getpid() and legacy.generation == generation:
                own_claim_exists = True
            elif process_alive(legacy.pid):
                return "busy", "otro indexador está arrancando"
            else:
                _unlink_startup_claim_paths((legacy_path,))
                recovered = True

        if own_claim_exists:
            return "claimed", "reclamación reservada" if recovered else ""
        _write_startup_claim(paths, WorkerOwner(os.getpid(), generation))
        return "claimed", "reclamación reservada" if recovered else ""
    except StartupClaimCleanupError as exc:
        log.warning("could not clean worker startup claim (%s)", type(exc.cause).__name__)
        return "failed", str(exc)
    except (OSError, ValueError) as exc:
        try:
            _unlink_startup_claim_paths(
                (
                    paths.startup_claim_file_for(os.getpid(), generation),
                    paths.startup_claim_temporary_file_for(os.getpid(), generation),
                )
            )
        except StartupClaimCleanupError as cleanup_error:
            log.warning(
                "could not clean worker startup claim (%s)",
                type(cleanup_error.cause).__name__,
            )
            return "failed", str(cleanup_error)
        log.warning("could not reserve worker startup claim (%s)", type(exc).__name__)
        return "failed", "no se pudo reservar el arranque del indexador"
    finally:
        lease.release()


def _startup_claim_blocks_worker(
    paths: AppPaths,
    selected_generation: str,
    alive_check,
) -> bool:
    selected_token = AppPaths.generation_filename_token(selected_generation)
    for path in _scoped_startup_claim_paths(paths):
        claim = _read_startup_claim_path(path)
        if claim is not None:
            if claim.generation == selected_generation:
                continue
            if alive_check(claim.pid):
                return True
            continue
        identity = _scoped_startup_claim_identity(path)
        if identity is None:
            raise LockError("could not identify a worker startup claim")
        pid, token = identity
        if token == selected_token:
            continue
        if alive_check(pid):
            return True
    legacy = _read_startup_claim_path(paths.startup_claim_file)
    if legacy is not None:
        return legacy.generation != selected_generation and alive_check(legacy.pid)
    if paths.startup_claim_file.exists():
        raise LockError("could not identify the legacy worker startup claim")
    return False


_held_worker_leases: dict[str, WorkerLease] = {}


class WorkerLease:
    """Serialize stale-lock recovery and hold ownership for the worker lifetime."""

    def __init__(self, paths: AppPaths, platform: str | None = None) -> None:
        self.paths = paths
        self.owner: WorkerOwner | None = None
        self._file_lease = FileLease(paths.worker_lease_file, platform=platform)

    @property
    def held(self) -> bool:
        return self._file_lease.held

    def acquire(
        self,
        generation: str | None = None,
        *,
        process_alive=None,
    ) -> WorkerOwner:
        if self.held:
            assert self.owner is not None
            return self.owner
        self.paths.ensure()
        selected_generation = (
            _normalise_generation(generation) or new_worker_generation()
        )
        alive_check = process_alive or globals()["process_alive"]
        if not self._file_lease.acquire():
            current_pid = read_lock_pid(self.paths)
            if current_pid is not None and alive_check(current_pid):
                raise WorkerAlreadyRunning(
                    f"indexer already running (pid {current_pid})"
                )
            raise LockError("could not acquire the indexer lease")

        wrote_lock = False
        try:
            if _startup_claim_blocks_worker(
                self.paths,
                selected_generation,
                alive_check,
            ):
                raise WorkerAlreadyRunning(
                    "another worker startup generation is still launching"
                )
            current_pid = read_lock_pid(self.paths)
            if current_pid is not None and alive_check(current_pid):
                raise WorkerAlreadyRunning(
                    f"indexer already running (pid {current_pid})"
                )
            if current_pid is not None:
                log.warning("removing stale indexer lock (pid=%s)", current_pid)
            try:
                self.paths.lock_file.unlink()
            except FileNotFoundError:
                pass
            try:
                self.paths.worker_owner_file.unlink()
            except FileNotFoundError:
                pass

            descriptor = os.open(
                self.paths.lock_file,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(str(os.getpid()))
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            wrote_lock = True
            owner = WorkerOwner(
                os.getpid(),
                selected_generation,
                current_process_creation_id(),
            )
            _write_worker_owner(self.paths, owner)
            _clear_startup_claim(self.paths, selected_generation)
            self.owner = owner
            return owner
        except Exception:
            if wrote_lock:
                _unlink_best_effort(self.paths.lock_file)
                _unlink_best_effort(self.paths.worker_owner_file)
            self._file_lease.release()
            raise

    def release(self) -> None:
        owner = self.owner
        try:
            if owner is not None and current_worker_owner(self.paths) == owner:
                _unlink_best_effort(self.paths.lock_file)
                _unlink_best_effort(self.paths.worker_owner_file)
        finally:
            self.owner = None
            self._file_lease.release()


# -- single instance lock ------------------------------------------------------

def process_alive(pid: int) -> bool:
    """Best-effort liveness check for another process."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    identity = open_process_identity(pid)
    if identity is not None:
        try:
            return bool(identity.alive())
        finally:
            identity.close()
    if sys.platform == "win32":
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_lock_pid(paths: AppPaths) -> int | None:
    try:
        raw = paths.lock_file.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def _write_worker_owner(paths: AppPaths, owner: WorkerOwner) -> None:
    paths.ensure()
    temporary = paths.worker_owner_file.with_name(
        paths.worker_owner_file.name + ".tmp"
    )
    payload = {
        "pid": owner.pid,
        "generation": owner.generation,
        "creation_id": owner.creation_id,
    }
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, paths.worker_owner_file)


def read_worker_owner(paths: AppPaths) -> WorkerOwner | None:
    try:
        raw = json.loads(paths.worker_owner_file.read_text(encoding="utf-8"))
        pid = raw.get("pid")
        generation = raw.get("generation")
        creation_id = raw.get("creation_id")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if generation is not None and (
            not isinstance(generation, str) or not generation.strip()
        ):
            return None
        if creation_id is not None and (
            not isinstance(creation_id, str) or not creation_id.strip()
        ):
            return None
        return WorkerOwner(pid, _normalise_generation(generation), creation_id)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def current_worker_owner(paths: AppPaths) -> WorkerOwner | None:
    pid = read_lock_pid(paths)
    if pid is None:
        return None
    owner = read_worker_owner(paths)
    if owner is not None and owner.pid == pid:
        return owner
    # PID-only locks are the phase-006 contract.  Treat them as legacy
    # identities, but callers must never assign such a worker to a tray.
    return WorkerOwner(pid, None)


def acquire_lock(paths: AppPaths, generation: str | None = None) -> int:
    """Atomically claim the PID lock while holding its race-safe OS lease."""
    paths.ensure()
    key = _lease_key(paths)
    if key in _held_worker_leases:
        previous = _held_worker_leases[key].owner
        pid = previous.pid if previous is not None else read_lock_pid(paths)
        raise WorkerAlreadyRunning(f"indexer already running (pid {pid})")
    selected = (
        _normalise_generation(generation)
        or _normalise_generation(os.environ.get(WORKER_GENERATION_ENV))
        or new_worker_generation()
    )
    lease = WorkerLease(paths)
    owner = lease.acquire(selected, process_alive=process_alive)
    _held_worker_leases[key] = lease
    return owner.pid


def release_lock(paths: AppPaths, pid: int | None = None) -> None:
    key = _lease_key(paths)
    lease = _held_worker_leases.get(key)
    if lease is None:
        return
    if pid is not None and (lease.owner is None or lease.owner.pid != int(pid)):
        return
    _held_worker_leases.pop(key, None)
    lease.release()


# -- status (atomic) ------------------------------------------------------------

def _replace_with_retry(temporary: Path, target: Path, attempts: int = 5) -> None:
    """``os.replace`` with a short retry.

    Windows can hold the destination open for a few milliseconds (a
    virus scanner, an indexer, the reader of the status file itself), and
    the worker writes this file continuously: a lost status write is a
    transient "the indexer looks stuck" for the user.
    """
    for attempt in range(attempts):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02)


def write_status(
    paths: AppPaths,
    state: str,
    *,
    error: str | None = None,
    stats: dict | None = None,
    roots: int | None = None,
    hotkey: str | None = None,
    generation: str | None = None,
) -> None:
    paths.ensure()
    previous = read_status(paths)
    timestamp = datetime.now(timezone.utc).isoformat()
    selected_generation = _normalise_generation(generation)
    if selected_generation is None:
        owner = read_worker_owner(paths)
        if owner is not None and owner.pid == os.getpid():
            selected_generation = owner.generation
    payload: dict = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": timestamp,
    }
    if selected_generation is not None:
        payload["generation"] = selected_generation
    if previous is not None:
        for key in ("last_scan_at", "last_scan_stats"):
            if key in previous:
                payload[key] = previous[key]
    if error is not None:
        payload["error"] = error
    if hotkey is not None:
        payload["hotkey"] = hotkey
    if stats is not None:
        payload["stats"] = stats
    if state == STATE_IDLE and stats is not None:
        payload["last_scan_at"] = timestamp
        payload["last_scan_stats"] = stats
    if roots is not None:
        payload["roots"] = roots
    temporary = paths.status_file.with_name(paths.status_file.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _replace_with_retry(temporary, paths.status_file)


def read_status(paths: AppPaths) -> dict | None:
    try:
        data = json.loads(paths.status_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        log.warning("corrupt status file at %s", paths.status_file)
        return None
    return data if isinstance(data, dict) else None


def clear_status(paths: AppPaths) -> None:
    try:
        paths.status_file.unlink()
    except FileNotFoundError:
        pass


# -- pause / stop markers --------------------------------------------------------

def pause(paths: AppPaths | None = None) -> None:
    paths = paths or AppPaths.discover()
    paths.ensure()
    paths.pause_file.touch()


def resume(paths: AppPaths | None = None) -> None:
    paths = paths or AppPaths.discover()
    try:
        paths.pause_file.unlink()
    except FileNotFoundError:
        pass


def is_paused(paths: AppPaths | None = None) -> bool:
    paths = paths or AppPaths.discover()
    return paths.pause_file.exists()


def request_stop(
    paths: AppPaths | None = None,
    *,
    owner: WorkerOwner | None = None,
    expected_generation: str | None = None,
) -> None:
    paths = paths or AppPaths.discover()
    paths.ensure()
    selected = owner or current_worker_owner(paths)
    requested_generation = _normalise_generation(expected_generation)
    if requested_generation is not None:
        if selected is not None and selected.generation != requested_generation:
            raise ValueError("stop request generation does not match its owner")
        if selected is None:
            selected = WorkerOwner(
                read_lock_pid(paths) or os.getpid(),
                requested_generation,
            )
    if selected is None:
        # Preserve the legacy empty marker for callers that request a stop
        # before any worker identity exists.
        paths.stop_file.touch()
        return
    target = paths.stop_request_file(selected.generation)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    payload = {"pid": selected.pid, "generation": selected.generation}
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        _unlink_best_effort(temporary)


def stop_requested(
    paths: AppPaths,
    *,
    owner: WorkerOwner | None = None,
    expected_generation: str | None = None,
) -> bool:
    selected = owner or current_worker_owner(paths)
    requested_generation = _normalise_generation(expected_generation)
    if requested_generation is not None:
        return paths.stop_request_file(requested_generation).exists()
    generation = selected.generation if selected is not None else None
    return paths.stop_request_file(generation).exists()


def clear_stop(
    paths: AppPaths,
    *,
    owner: WorkerOwner | None = None,
    expected_generation: str | None = None,
) -> None:
    selected = owner or current_worker_owner(paths)
    requested_generation = _normalise_generation(expected_generation)
    if requested_generation is not None:
        _unlink_best_effort(paths.stop_request_file(requested_generation))
        return
    generation = selected.generation if selected is not None else None
    _unlink_best_effort(paths.stop_request_file(generation))


# -- autostart (registry injected for tests) -------------------------------------

def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" indexer run'
    return f'"{sys.executable}" -m universal_search.cli indexer run'


def set_autostart(enabled: bool, registry=None) -> None:
    """Register/unregister the Run-key entry through the platform adapter."""
    from universal_search.platforms.windows import set_autostart as platform_set

    platform_set(enabled, registry=registry)


def get_autostart(registry=None) -> bool:
    from universal_search.platforms.windows import autostart_enabled

    return autostart_enabled(registry=registry)


# -- external control ------------------------------------------------------------

def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "indexer", "run"]
    return [sys.executable, "-m", "universal_search.cli", "indexer", "run"]


def _startup_cleanup_error(
    paths: AppPaths,
    generation: str,
    *,
    parent_pid: int,
) -> str | None:
    try:
        _clear_startup_claim(
            paths,
            generation,
            parent_pid=parent_pid,
        )
    except StartupClaimCleanupError as exc:
        log.warning("could not clean worker startup claim (%s)", type(exc.cause).__name__)
        return str(exc)
    return None


def start(
    paths: AppPaths | None = None,
    *,
    wait: float = 10.0,
    generation: str | None = None,
) -> tuple[str, str]:
    """Spawn a worker and wait for its exact startup generation identity.

    ``Popen.pid`` is deliberately not used as proof of ownership: a Windows
    virtual-environment launcher may own that PID while the child interpreter
    records a different one.  The token reaches the child through the
    environment and is accepted only when lock ownership and status agree.
    """
    paths = paths or AppPaths.discover()
    selected_generation = _normalise_generation(generation) or new_worker_generation()
    owner = current_worker_owner(paths)
    if owner is not None and process_alive(owner.pid):
        return "already-running", f"indexador ya en marcha (pid {owner.pid})"
    claim_state, claim_message = _acquire_startup_claim(
        paths,
        selected_generation,
    )
    if claim_state == "busy":
        return "already-running", claim_message
    if claim_state != "claimed":
        return "failed", claim_message
    command = _worker_command()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    note = ""
    if not AppConfig.load(paths).roots:
        note = " — sin carpetas configuradas"
    try:
        environment = {
            **os.environ,
            "UNIVERSAL_SEARCH_HOME": str(paths.home),
            WORKER_GENERATION_ENV: selected_generation,
        }
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(paths.home if paths.home.exists() else Path.cwd()),
            env=environment,
            creationflags=creationflags,
        )
    except OSError as exc:
        cleanup_error = _startup_cleanup_error(
            paths,
            selected_generation,
            parent_pid=os.getpid(),
        )
        if cleanup_error is not None:
            return "failed", cleanup_error
        log.warning("could not spawn indexer (%s)", type(exc).__name__)
        return "failed", f"no se pudo iniciar: {type(exc).__name__}"
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        owner = current_worker_owner(paths)
        status = read_status(paths)
        if owner is not None and process_alive(owner.pid):
            if owner.generation == selected_generation:
                if (
                    status is not None
                    and status.get("pid") == owner.pid
                    and status.get("generation") == selected_generation
                ):
                    return "started", f"indexador iniciado (pid {owner.pid}){note}"
            elif owner.generation is not None:
                cleanup_error = _startup_cleanup_error(
                    paths,
                    selected_generation,
                    parent_pid=os.getpid(),
                )
                if cleanup_error is not None:
                    return "failed", cleanup_error
                return "already-running", f"indexador ya en marcha (pid {owner.pid})"
            # A child briefly owns the PID-only lock before publishing the
            # generation sidecar.  Treat that transitional legacy view as
            # pending instead of reporting our own startup as a replacement.
        # ``Popen.pid`` may be a short-lived Windows venv launcher.  Its exit
        # is not evidence that the worker failed; only the exact generation
        # handshake above can complete or reject this startup.
        time.sleep(0.1)
    owner = current_worker_owner(paths)
    if owner is not None and process_alive(owner.pid):
        cleanup_error = _startup_cleanup_error(
            paths,
            selected_generation,
            parent_pid=os.getpid(),
        )
        if cleanup_error is not None:
            return "failed", cleanup_error
        return "already-running", f"indexador ya en marcha (pid {owner.pid})"
    if process.poll() is not None:
        log.warning("indexer launcher exited with code %s", process.returncode)
    cleanup_error = _startup_cleanup_error(
        paths,
        selected_generation,
        parent_pid=os.getpid(),
    )
    if cleanup_error is not None:
        return "failed", cleanup_error
    return "failed", f"el trabajador no respondió en {wait:.0f}s"


def _creation_identity_mismatch(
    owner: WorkerOwner,
    identity: ProcessIdentity | None,
) -> bool:
    if identity is None or identity.pid != owner.pid:
        return identity is not None
    owner_creation = owner.creation_id
    identity_creation = getattr(identity, "creation_id", None)
    return bool(
        owner_creation
        and identity_creation
        and owner_creation != identity_creation
    )


def _identity_can_be_force_terminated(
    owner: WorkerOwner,
    identity: ProcessIdentity | None,
) -> bool:
    if identity is None or identity.pid != owner.pid:
        return False
    owner_creation = owner.creation_id
    identity_creation = getattr(identity, "creation_id", None)
    if owner_creation or identity_creation:
        return bool(
            owner_creation
            and identity_creation
            and owner_creation == identity_creation
        )
    # A pidfd is itself a kernel-bound process-creation identity.  Windows
    # requires the recorded FILETIME because a bare handle is not persisted in
    # the owner sidecar and therefore cannot be rebound across the open call.
    return getattr(identity, "kind", None) == "pidfd"


def stop(
    paths: AppPaths | None = None,
    *,
    timeout: float = 8.0,
    expected_pid: int | None = None,
    expected_generation: str | None = None,
) -> tuple[str, str]:
    """Stop only the requested worker PID/generation.

    The marker names the observed owner, and cleanup is conditional on that
    same owner.  If graceful stop times out, termination uses a process handle
    opened for that PID, so PID reuse cannot redirect SIGTERM to a replacement.
    """

    paths = paths or AppPaths.discover()
    requested_expected_generation = _normalise_generation(expected_generation)

    def matches_expected(owner: WorkerOwner | None) -> bool:
        if expected_pid is None:
            return requested_expected_generation is None
        if owner is None or owner.pid != expected_pid:
            return False
        return (
            requested_expected_generation is None
            or owner.generation == requested_expected_generation
        )

    def replacement_result(owner: WorkerOwner | None) -> tuple[str, str]:
        if owner is not None and process_alive(owner.pid):
            return "replaced", f"el indexador fue reemplazado (pid {owner.pid})"
        return "not-running", "el indexador esperado ya no está en marcha"

    owner = current_worker_owner(paths)
    if not matches_expected(owner):
        return replacement_result(owner)
    if owner is None or not process_alive(owner.pid):
        if owner is not None:
            clear_stop(paths, owner=owner)
        return "not-running", "el indexador no está en marcha"

    identity: ProcessIdentity | None = open_process_identity(owner.pid)

    def finish(result: tuple[str, str]) -> tuple[str, str]:
        if identity is not None:
            identity.close()
        return result

    def owned_process_alive() -> bool:
        return (
            bool(identity.alive())
            if identity is not None
            else process_alive(owner.pid)
        )

    confirmed = current_worker_owner(paths)
    if not matches_expected(confirmed) or confirmed != owner:
        return finish(replacement_result(confirmed))
    if confirmed is None:
        return finish(replacement_result(None))
    if _creation_identity_mismatch(confirmed, identity):
        return finish(replacement_result(confirmed))
    force_identity_bound = _identity_can_be_force_terminated(confirmed, identity)

    request_stop(paths, owner=confirmed)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = current_worker_owner(paths)
        if current is None:
            if owned_process_alive():
                time.sleep(0.2)
                continue
            clear_stop(paths, owner=confirmed)
            return finish(("stopped", "indexador detenido"))
        if current != confirmed:
            return finish(replacement_result(current))
        alive = owned_process_alive()
        if not alive:
            clear_stop(paths, owner=confirmed)
            return finish(("stopped", "indexador detenido"))
        time.sleep(0.2)

    current = current_worker_owner(paths)
    if current is None:
        if not owned_process_alive():
            clear_stop(paths, owner=confirmed)
            return finish(("stopped", "indexador detenido"))
        return finish(
            (
                "failed",
                f"no se detuvo de forma segura el proceso {confirmed.pid}: "
                "se perdió el registro de propietario",
            )
        )
    if current != confirmed:
        return finish(replacement_result(current))
    alive = (
        bool(identity.alive())
        if identity is not None
        else process_alive(confirmed.pid)
    )
    if not alive:
        clear_stop(paths, owner=confirmed)
        return finish(("not-running", "el indexador esperado ya no está en marcha"))

    if not force_identity_bound:
        log.warning(
            "worker %s ignored its scoped stop request; no bound creation identity",
            confirmed.pid,
        )
        clear_stop(paths, owner=confirmed)
        return finish(
            (
                "failed",
                f"no se pudo detener de forma segura el proceso {confirmed.pid}: "
                "identidad de creación no comprobada",
            )
        )

    verified_owner = current_worker_owner(paths)
    if verified_owner != confirmed or not _identity_can_be_force_terminated(
        confirmed,
        identity,
    ):
        return finish(replacement_result(verified_owner))
    log.warning("worker %s ignored the stop file; terminating", confirmed.pid)
    if not terminate_process_identity(identity):
        clear_stop(paths, owner=confirmed)
        return finish(("failed", f"no se pudo detener el proceso {confirmed.pid}"))
    force_deadline = time.monotonic() + 3.0
    while time.monotonic() < force_deadline:
        current = current_worker_owner(paths)
        if current is None:
            if identity.alive():
                time.sleep(0.2)
                continue
            clear_stop(paths, owner=confirmed)
            return finish(("stopped", "indexador detenido"))
        if current != confirmed:
            return finish(replacement_result(current))
        if not identity.alive():
            clear_stop(paths, owner=confirmed)
            return finish(("stopped", "indexador detenido (terminado a la fuerza)"))
        time.sleep(0.2)
    clear_stop(paths, owner=confirmed)
    return finish(("failed", f"no se pudo detener el proceso {confirmed.pid}"))


def status_report(paths: AppPaths | None = None) -> str:
    paths = paths or AppPaths.discover()
    owner = current_worker_owner(paths)
    pid = owner.pid if owner is not None else None
    alive = pid is not None and process_alive(pid)
    raw_status = read_status(paths)
    status_generation = (
        _normalise_generation(raw_status.get("generation"))
        if isinstance(raw_status, dict)
        else None
    )
    status_matches = (
        alive
        and owner is not None
        and isinstance(raw_status, dict)
        and raw_status.get("pid") == owner.pid
        and status_generation == owner.generation
    )
    status = raw_status if status_matches else None
    if status is None and not alive:
        if is_paused(paths):
            return "indexador: detenido (quedó en pausa; se iniciará pausado)"
        return "indexador: detenido"
    state = (status or {}).get("state", "iniciando")
    lines = [f"indexador: {state} (pid {pid if alive else 'sin proceso'})"]
    if status:
        if "updated_at" in status:
            lines.append(f"  actualizado: {status['updated_at']}")
        if "roots" in status:
            lines.append(f"  carpetas: {status['roots']}")
        if "stats" in status:
            stats = status["stats"]
            lines.append(
                "  ultimo paso: "
                + " ".join(f"{key}={value}" for key, value in stats.items())
            )
        if "error" in status:
            lines.append(f"  error: {status['error']}")
    if is_paused(paths):
        lines.append("  pausado por marcador")
    return "\n".join(lines)


# -- the worker -------------------------------------------------------------------

class BackgroundIndexer:
    """Cooperative worker: initial reconciliation, watchers, periodic passes."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        *,
        stop_event: threading.Event | None = None,
        interval: float | None = None,
        file_delay: float | None = None,
        observers: bool = True,
        hotkey_server=None,
    ) -> None:
        self.paths = paths or AppPaths.discover()
        self.stop_event = stop_event or threading.Event()
        self.config = AppConfig.load(self.paths)
        self.interval = (
            self.config.indexer_interval_seconds if interval is None else interval
        )
        self.file_delay = (
            self.config.indexer_file_delay if file_delay is None else file_delay
        )
        self.database = SearchDatabase(self.paths.database)
        self.indexer = Indexer(self.database)
        self.use_observers = observers
        self.hotkey_server = hotkey_server  # tests inject a fake server
        self._observer = None
        self._dirty = threading.Event()
        self._last_pass = 0.0
        self._last_state: str | None = None
        self._owner: WorkerOwner | None = None

    # -- filesystem watchers ----------------------------------------------------

    def _is_own_file(self, path: str | None) -> bool:
        """Ignore events caused by the indexer writing its own files."""
        if not path:
            return False
        try:
            Path(path).resolve().relative_to(self.paths.home.resolve())
        except (ValueError, OSError):
            return False
        return True

    def _start_observers(self) -> None:
        if not self.use_observers:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:  # pragma: no cover - watchdog is a declared dependency
            log.warning("watchdog unavailable; periodic reconciliation only")
            return

        worker = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: D401 - watchdog hook
                source = getattr(event, "dest_path", None) or event.src_path
                if worker._is_own_file(source):
                    return
                worker._dirty.set()

        observer = Observer()
        scheduled = 0
        for root in configured_roots(self.config):
            root_path = Path(root)
            if root_path.is_dir():
                observer.schedule(_Handler(), str(root_path), recursive=True)
                scheduled += 1
        if scheduled:
            observer.start()
            self._observer = observer
            log.info("watching %d root(s)", scheduled)

    def _stop_observers(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:  # pragma: no cover - best effort on shutdown
                log.exception("observer shutdown failed")
            self._observer = None

    # -- global hotkey ------------------------------------------------------------

    def _start_hotkey(self) -> None:
        """Register the global shortcut; failures never stop indexing."""
        if not self.config.hotkey_enabled:
            return
        server = self.hotkey_server
        if server is None:
            try:
                server = HotkeyServer(self.config.hotkey, self._on_hotkey_press)
            except ValueError as exc:
                log.warning(
                    "hotkey inválido en la configuración (%s); atajo desactivado",
                    exc,
                )
                return
            self.hotkey_server = server
        try:
            started = server.start()
        except Exception:
            log.exception("no se pudo iniciar el atajo global")
            return
        if not started:
            log.warning(
                "atajo global no disponible (%s)",
                getattr(server, "error", None) or getattr(server, "spec", "?"),
            )

    def _stop_hotkey(self) -> None:
        if self.hotkey_server is not None:
            try:
                self.hotkey_server.stop()
            except Exception:  # pragma: no cover - best effort shutdown
                log.exception("hotkey shutdown failed")

    def _on_hotkey_press(self) -> None:
        """Present the running window, or launch one when there is none."""
        if request_show(self.paths):
            return
        launch_gui()

    def _hotkey_problem(self) -> str | None:
        """Surface a hotkey that could not be registered (spec 016).

        A silent hotkey is the worst outcome: the user presses the key and
        nothing happens. The worker records the reason in its status file
        so the CLI, the GUI and `diagnose health` can all show it.
        """
        server = self.hotkey_server
        if server is None or server.registered or not server.error:
            return None
        return str(server.error)

    # -- status -------------------------------------------------------------------

    def _set_state(self, state: str, **extra) -> None:
        if state == self._last_state and not extra:
            return
        self._last_state = state
        try:
            # A hotkey that could not be registered is reported here so it
            # is visible in `indexer status`, the GUI and diagnostics,
            # instead of being a key that silently does nothing (spec 016).
            write_status(
                self.paths,
                state,
                hotkey=self._hotkey_problem(),
                generation=self._owner.generation if self._owner is not None else None,
                **extra,
            )
        except OSError:
            log.exception("could not write status")

    # -- reconciliation ------------------------------------------------------------

    def reconcile(self) -> bool:
        """One full pass over every configured root.

        Returns False when interrupted by stop/pause; errors in one root do
        not prevent the remaining roots from being indexed.
        """
        # Local metrics sink (spec 011): every pass records duration and
        # counters into the user's metrics.jsonl — counters only, no paths
        # or content.
        set_sink(self.paths.metrics_file)
        self._last_pass = time.monotonic()
        config = AppConfig.load(self.paths)
        self.config = config
        roots = [Path(root) for root in configured_roots(config)]
        rules = config.ignore_rules()
        self._set_state(STATE_INDEXING, roots=len(roots))
        totals = IndexStats()
        failure: str | None = None
        for root in roots:
            if (
                self.stop_event.is_set()
                or stop_requested(self.paths, owner=self._owner)
                or is_paused(self.paths)
            ):
                break
            try:
                totals.merge(
                    self.indexer.index_root(
                        root,
                        rules=rules,
                        delay=self.file_delay,
                        onedrive_download_mb=config.onedrive_download_max_mb,
                    )
                )
            except Exception as exc:
                log.exception("reconciliation failed for %s", root)
                failure = f"{type(exc).__name__}: {exc}"
        if failure is not None:
            self._set_state(
                STATE_ERROR, error=failure, stats=totals.as_dict(), roots=len(roots)
            )
            return False
        self._set_state(STATE_IDLE, stats=totals.as_dict(), roots=len(roots))
        return True

    # -- lifecycle -------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def handler(_signum, _frame):
            self.stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover - not main thread
                pass

    def run(self) -> int:
        try:
            acquire_lock(self.paths)
            owner = read_worker_owner(self.paths)
            if owner is None or owner.pid != os.getpid():
                release_lock(self.paths, os.getpid())
                raise LockError("the acquired worker lock has no valid owner")
            self._owner = owner
        except StartupClaimCleanupError as exc:
            log.warning(str(exc))
            return EXIT_CRASH
        except WorkerAlreadyRunning as exc:
            log.info(str(exc))
            return EXIT_ALREADY_RUNNING
        crashed = False
        try:
            clear_stop(self.paths, owner=self._owner)
            self._install_signal_handlers()
            self._start_observers()
            self._start_hotkey()
            self._set_state(STATE_IDLE, roots=len(configured_roots(self.config)))
            # Initial reconciliation scan (spec requirement).
            self.reconcile()
            was_paused = is_paused(self.paths)
            while not self.stop_event.is_set() and not stop_requested(
                self.paths,
                owner=self._owner,
            ):
                if is_paused(self.paths):
                    self._set_state(STATE_PAUSED)
                    was_paused = True
                    self.stop_event.wait(STOP_POLL_SECONDS)
                    continue
                if was_paused:
                    was_paused = False
                    self._dirty.set()  # resume -> reconcile immediately
                now = time.monotonic()
                due_watch = self._dirty.is_set() and (
                    now - self._last_pass
                ) >= MIN_WATCH_INTERVAL
                due_period = (now - self._last_pass) >= self.interval
                if due_watch or due_period:
                    self._dirty.clear()
                    self.reconcile()
                    continue
                self.stop_event.wait(STOP_POLL_SECONDS)
            log.info("indexer stopping cleanly")
            return EXIT_OK
        except KeyboardInterrupt:  # pragma: no cover - foreground Ctrl+C
            log.info("interrupted; shutting down")
            return EXIT_OK
        except Exception as exc:
            crashed = True
            log.exception("indexer crashed")
            try:
                write_status(
                    self.paths,
                    STATE_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                    generation=self._owner.generation,
                )
            except OSError:
                pass
            return EXIT_CRASH
        finally:
            self._stop_observers()
            self._stop_hotkey()
            if self._owner is not None:
                clear_stop(self.paths, owner=self._owner)
                if not crashed:
                    status = read_status(self.paths)
                    if (
                        status is not None
                        and status.get("pid") == self._owner.pid
                        and status.get("generation") == self._owner.generation
                    ):
                        clear_status(self.paths)
                release_lock(self.paths, self._owner.pid)
            self._owner = None
