"""Global hotkey: registration, show-request signaling, GUI launch.

Registration runs on a dedicated thread with its own Win32 message queue
(``RegisterHotKey`` needs one). Every Win32 call goes through injectable
DLL objects so the whole lifecycle is unit-testable without grabbing a
real hotkey.

Signaling between the background worker and the desktop window is
file-based, the same pattern the worker already uses for status/stop/pause:

    gui.pid         PID of the live desktop window (written by the window)
    gui-show.flag   touched by the worker; the window consumes it and
                    lifts itself

When ``gui.pid`` names a live process the worker only touches the flag;
otherwise it launches a new GUI process. None of this is needed to
search — the hotkey is presentation sugar over the existing engine.
"""

import ctypes
import logging
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from universal_search.appconfig import AppPaths

log = logging.getLogger("universal_search.hotkey")

# -- Win32 constants -----------------------------------------------------------

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
HOTKEY_ID = 0x5353  # arbitrary per-thread id

_MODIFIER_ALIASES = {
    "alt": MOD_ALT,
    "menu": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "grave": 0xC0,
    "`": 0xC0,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """``'ctrl+alt+s'`` → ``(MOD_CONTROL | MOD_ALT, ord('S'))``.

    A readable ``ValueError`` for unusable specs guarantees that a bad
    configuration can never take the worker down: callers disable the
    hotkey and carry on. At least one modifier is required so a bare
    letter can never swallow normal typing if registration succeeds.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("atajo vacío")
    parts = [part.strip().lower() for part in spec.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"atajo inválido: {spec!r}")
    *modifier_parts, key_part = parts
    if not modifier_parts:
        raise ValueError(f"falta un modificador en: {spec!r}")
    modifiers = 0
    for name in modifier_parts:
        if name not in _MODIFIER_ALIASES:
            raise ValueError(f"modificador desconocido: {name!r}")
        modifiers |= _MODIFIER_ALIASES[name]
    return modifiers, _parse_key(key_part)


def _parse_key(part: str) -> int:
    if part in _NAMED_KEYS:
        return _NAMED_KEYS[part]
    if len(part) == 1 and part.isalpha():
        return ord(part.upper())
    if len(part) == 1 and part.isdigit():
        return ord(part)
    if part.startswith("f") and part[1:].isdigit():
        number = int(part[1:])
        if 1 <= number <= 12:
            return 0x70 + number - 1
    raise ValueError(f"tecla desconocida: {part!r}")


# -- the registration thread ---------------------------------------------------

class HotkeyServer:
    """Registers ``spec`` for this session and dispatches presses.

    ``start()`` returns ``False`` when Windows refuses the registration
    (hotkey already taken by another application) — never raises for that
    case, because the worker must keep indexing regardless.
    """

    def __init__(
        self,
        spec: str,
        on_pressed,
        *,
        user32=None,
        kernel32=None,
    ) -> None:
        self.spec = spec
        self.on_pressed = on_pressed
        self._user32 = user32
        self._kernel32 = kernel32
        self._modifiers, self._vk = parse_hotkey(spec)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self.registered = False
        self.error: str | None = None

    # -- lifecycle -------------------------------------------------------------

    def start(self, timeout: float = 2.0) -> bool:
        if self._thread is not None:
            return self.registered
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="universal-search-hotkey", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout)
        return self.registered

    def stop(self, timeout: float = 3.0) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._thread_id is not None:
            user32, _kernel32 = self._dlls()
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:  # pragma: no cover - best effort shutdown
                log.exception("could not post WM_QUIT to the hotkey thread")
        thread.join(timeout)
        self._thread_id = None
        self.registered = False

    # -- internals -------------------------------------------------------------

    def _dlls(self):
        if self._user32 is None or self._kernel32 is None:
            return ctypes.windll.user32, ctypes.windll.kernel32
        return self._user32, self._kernel32

    def _run(self) -> None:
        user32, kernel32 = self._dlls()
        message = wintypes.MSG()
        try:
            # Create the message queue first: PostThreadMessageW silently
            # fails against a thread that has not pumped messages yet.
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
            self._thread_id = kernel32.GetCurrentThreadId()
            if user32.RegisterHotKey(None, HOTKEY_ID, self._modifiers, self._vk):
                self.registered = True
                log.info("global hotkey registered: %s", self.spec)
            else:
                self.error = f"no se pudo registrar {self.spec} (¿ocupado?)"
                log.warning(self.error)
        except Exception:
            self.error = "falló el registro del atajo global"
            log.exception("hotkey registration failed")
        finally:
            self._ready.set()
        if not self.registered:
            return
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:  # 0 → WM_QUIT, -1 → error
                    break
                if message.message == WM_HOTKEY:
                    self._dispatch()
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:  # pragma: no cover - loop must never kill the worker
            log.exception("hotkey loop crashed")
        finally:
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:  # pragma: no cover - best effort
                log.exception("UnregisterHotKey failed")
            self.registered = False
            log.info("global hotkey released")

    def _dispatch(self) -> None:
        try:
            self.on_pressed()
        except Exception:  # pragma: no cover - presentation only, keep alive
            log.exception("hotkey handler failed")


# -- file-based signaling with the desktop window -----------------------------

def write_gui_pid(paths: AppPaths, pid: int | None = None) -> None:
    """Publish the desktop window PID for the worker."""
    paths.ensure()
    paths.gui_pid_file.write_text(str(pid or _current_pid()), encoding="utf-8")


def read_gui_pid(paths: AppPaths) -> int | None:
    try:
        return int(paths.gui_pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def clear_gui_pid(paths: AppPaths) -> None:
    try:
        paths.gui_pid_file.unlink()
    except OSError:  # pragma: no cover - already gone
        pass


def request_show(paths: AppPaths) -> bool:
    """Ask a *live* window to present itself; True when it will see the flag."""
    pid = read_gui_pid(paths)
    if pid is None or not _process_alive(pid):
        return False
    paths.ensure()
    paths.show_request_file.touch()
    return True


def consume_show_request(paths: AppPaths) -> bool:
    """Consume the show-request flag; True exactly once per request."""
    try:
        paths.show_request_file.unlink()
        return True
    except OSError:
        return False


def _process_alive(pid: int) -> bool:
    # Late import: background imports this module at load time.
    from universal_search.background import process_alive

    return process_alive(pid)


def _current_pid() -> int:
    import os

    return os.getpid()


# -- launching -----------------------------------------------------------------

def gui_command() -> list[str]:
    """Command that starts the desktop window (packaged or from source)."""
    if getattr(sys, "frozen", False):
        sibling = Path(sys.executable).with_name("UniversalSearch.exe")
        if sibling.exists():  # windowed exe: no console flash
            return [str(sibling)]
        return [sys.executable, "gui"]
    return [sys.executable, "-m", "universal_search.cli", "gui"]


def launch_gui() -> None:
    """Start the search window without blocking the worker."""
    command = gui_command()
    log.info("launching GUI: %s", command)
    subprocess.Popen(command)
