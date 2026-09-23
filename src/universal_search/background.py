"""Background indexer: single-instance lifecycle, status, pause/stop, autostart.

The worker runs as a separate process (``universal-search indexer run``) so
closing the GUI never stops indexing. Coordination with the outside world
happens exclusively through files in the per-user application directory:

    indexer.lock         PID of the running worker (single instance)
    indexer-status.json  atomically written state (idle/indexing/paused/error)
    indexer-paused.flag  presence == paused
    indexer-stop.flag    presence == requested to stop

Crash-safe writes come from the database layer (SQLite WAL + one transaction
per file); status JSON writes are atomic via ``os.replace``.

Responsiveness comes from filesystem notifications (watchdog), but periodic
reconciliation remains the correctness mechanism: notifications are only an
optimization and may be missed.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.context import configured_roots
from universal_search.hotkey import HotkeyServer, launch_gui, request_show
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import IndexStats, Indexer
from universal_search.metrics import set_sink

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
# kernel32 constants
_WAIT_TIMEOUT = 0x0102
_PROCESS_SYNCHRONIZE = 0x00100000
# creation flags for the detached worker process
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class WorkerAlreadyRunning(RuntimeError):
    """Another live worker holds the lock."""


class LockError(RuntimeError):
    """The lock could not be acquired."""


# -- single instance lock ------------------------------------------------------

def process_alive(pid: int) -> bool:
    """Best-effort liveness check for another process."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(_PROCESS_SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT means the process object never signalled: still alive.
            return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
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


def acquire_lock(paths: AppPaths) -> int:
    """Atomically create the lock file, replacing stale locks."""
    paths.ensure()
    for _attempt in range(3):
        try:
            descriptor = os.open(paths.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = read_lock_pid(paths)
            if pid is not None and process_alive(pid):
                raise WorkerAlreadyRunning(f"indexer already running (pid {pid})")
            log.warning("removing stale indexer lock (pid=%s)", pid)
            try:
                paths.lock_file.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        return os.getpid()
    raise LockError("could not acquire the indexer lock")


def release_lock(paths: AppPaths, pid: int | None = None) -> None:
    current = read_lock_pid(paths)
    if current is None:
        return
    if current == (pid if pid is not None else os.getpid()):
        try:
            paths.lock_file.unlink()
        except FileNotFoundError:
            pass


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
) -> None:
    paths.ensure()
    payload: dict = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error is not None:
        payload["error"] = error
    if hotkey is not None:
        payload["hotkey"] = hotkey
    if stats is not None:
        payload["stats"] = stats
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


def request_stop(paths: AppPaths | None = None) -> None:
    paths = paths or AppPaths.discover()
    paths.ensure()
    paths.stop_file.touch()


def stop_requested(paths: AppPaths) -> bool:
    return paths.stop_file.exists()


def clear_stop(paths: AppPaths) -> None:
    try:
        paths.stop_file.unlink()
    except FileNotFoundError:
        pass


# -- autostart (registry injected for tests) -------------------------------------

def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" indexer run'
    return f'"{sys.executable}" -m universal_search.cli indexer run'


def _registry():
    import winreg

    return winreg


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


def start(paths: AppPaths | None = None, *, wait: float = 10.0) -> tuple[str, str]:
    """Spawn a detached worker. Returns (state, message).

    States: ``started``, ``already-running``, ``failed``.

    ``wait`` bounds the handshake (spawn + first lock/status write).
    Five seconds was enough on an idle machine and too tight on a loaded
    one, where a correct worker was reported as failed; ten seconds is
    still imperceptible to the person who pressed the button.
    """
    paths = paths or AppPaths.discover()
    pid = read_lock_pid(paths)
    if pid is not None and process_alive(pid):
        return "already-running", f"indexador ya en marcha (pid {pid})"
    clear_stop(paths)
    command = _worker_command()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    note = ""
    if not AppConfig.load(paths).roots:
        note = " — sin carpetas configuradas"
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(paths.home if paths.home.exists() else Path.cwd()),
            creationflags=creationflags,
        )
    except OSError as exc:
        log.exception("could not spawn indexer")
        return "failed", f"no se pudo iniciar: {exc}"
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        current = read_lock_pid(paths)
        if current is not None and process_alive(current):
            return "started", f"indexador iniciado (pid {current}){note}"
        if process.poll() is not None:
            output = b""
            if process.stdout is not None:
                output = process.stdout.read() or b""
            detail = output.decode("utf-8", "replace").strip()[-400:]
            log.error("indexer exited immediately: %s", detail)
            return "failed", f"el trabajador terminó al arrancar: {detail or process.returncode}"
        time.sleep(0.1)
    return "failed", f"el trabajador no respondió en {wait:.0f}s"


def stop(paths: AppPaths | None = None, *, timeout: float = 8.0) -> tuple[str, str]:
    """Ask the worker to stop; escalate to termination if it ignores us."""
    paths = paths or AppPaths.discover()
    pid = read_lock_pid(paths)
    if pid is None or not process_alive(pid):
        clear_stop(paths)
        return "not-running", "el indexador no está en marcha"
    request_stop(paths)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_lock_pid(paths)
        if current is None or not process_alive(current):
            clear_stop(paths)
            return "stopped", "indexador detenido"
        time.sleep(0.2)
    log.warning("worker %s ignored the stop file; terminating", pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    force_deadline = time.monotonic() + 3.0
    while time.monotonic() < force_deadline:
        if not process_alive(pid):
            clear_stop(paths)
            return "stopped", "indexador detenido (terminado a la fuerza)"
        time.sleep(0.2)
    clear_stop(paths)
    return "failed", f"no se pudo detener el proceso {pid}"


def status_report(paths: AppPaths | None = None) -> str:
    paths = paths or AppPaths.discover()
    pid = read_lock_pid(paths)
    alive = pid is not None and process_alive(pid)
    status = read_status(paths)
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
            write_status(self.paths, state, hotkey=self._hotkey_problem(), **extra)
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
                or stop_requested(self.paths)
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
        except WorkerAlreadyRunning as exc:
            log.info(str(exc))
            return EXIT_ALREADY_RUNNING
        crashed = False
        try:
            clear_stop(self.paths)
            self._install_signal_handlers()
            self._start_observers()
            self._start_hotkey()
            self._set_state(STATE_IDLE, roots=len(configured_roots(self.config)))
            # Initial reconciliation scan (spec requirement).
            self.reconcile()
            was_paused = is_paused(self.paths)
            while not self.stop_event.is_set() and not stop_requested(self.paths):
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
                    self.paths, STATE_ERROR, error=f"{type(exc).__name__}: {exc}"
                )
            except OSError:
                pass
            return EXIT_CRASH
        finally:
            self._stop_observers()
            self._stop_hotkey()
            release_lock(self.paths)
            clear_stop(self.paths)
            if not crashed:
                clear_status(self.paths)
