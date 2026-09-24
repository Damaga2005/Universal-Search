"""Optional Windows notification-area backend for the tray controller.

The public surface is platform-independent.  Win32 types and DLL loading stay
inside this module and are resolved lazily so importing the adapter is safe on
every platform.
"""

from __future__ import annotations

import logging
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


log = logging.getLogger("universal_search.platforms.tray")

__all__ = [
    "MenuItem",
    "TrayBackend",
    "TrayProcessLock",
    "NullTray",
    "WindowsTray",
]

_Menu = Sequence["MenuItem"] | Callable[[], Sequence["MenuItem"]]

# Win32 protocol values.  They are implementation details: controllers exchange
# MenuItem command values and never need to know these constants.
_WM_DESTROY = 0x0002
_WM_NULL = 0x0000
_WM_CONTEXTMENU = 0x007B
_WM_COMMAND = 0x0111
_WM_TIMER = 0x0113
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONUP = 0x0205
_WM_APP = 0x8000
_TRAY_CALLBACK = _WM_APP + 1
_CMD_OPEN = 1
_CMD_EXIT = 9

_NIM_ADD = 0x00000000
_NIM_MODIFY = 0x00000001
_NIM_DELETE = 0x00000002
_NIF_MESSAGE = 0x00000001
_NIF_ICON = 0x00000002
_NIF_TIP = 0x00000004
_NIF_INFO = 0x00000010
_NIIF_INFO = 0x00000001

_MF_STRING = 0x0000
_MF_GRAYED = 0x0001
_MF_SEPARATOR = 0x0800
_TPM_RIGHTBUTTON = 0x0002
_TPM_RETURNCMD = 0x0100

_IDI_APPLICATION = 32512
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_TIMER_INTERVAL_MS = 1000


@dataclass(frozen=True, slots=True)
class MenuItem:
    """One native popup-menu entry."""

    command: int
    label: str
    enabled: bool = True
    separator: bool = False


class TrayProcessLock:
    """Hold an OS-backed exclusive lock while retaining PID diagnostics.

    The PID path is intentionally never unlinked.  Ownership is represented by
    the held file lock, so a dead or cleanly stopped owner can be replaced
    without a check/delete race, and the last PID remains available for
    diagnostics.
    """

    def __init__(self, path: Path, platform: str | None = None) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._platform = sys.platform if platform is None else platform
        self._file: Any = None

    def acquire(self) -> bool:
        if self._file is not None:
            return True
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return False

        try:
            lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            return False

        try:
            if os.fstat(lock_file.fileno()).st_size == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if self._platform == "win32":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError, ValueError):
            lock_file.close()
            return False

        self._file = lock_file
        return True

    def read_pid(self) -> int | None:
        if self._file is None:
            return None
        try:
            value = self.path.read_text(encoding="ascii").strip()
            return int(value) if value else None
        except (OSError, UnicodeError, ValueError):
            return None

    def write_pid(self, pid: int) -> None:
        if self._file is None:
            raise RuntimeError("the tray process lock is not held")
        data = str(int(pid)).encode("ascii")
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as pid_file:
                pid_file.write(data)
                pid_file.flush()
                try:
                    os.fsync(pid_file.fileno())
                except OSError:
                    pass
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def release(self) -> None:
        lock_file, self._file = self._file, None
        if lock_file is None:
            return
        try:
            lock_file.seek(0)
            if self._platform == "win32":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError, ValueError):
            pass
        finally:
            lock_file.close()


class TrayBackend(ABC):
    """Backend operations used by the platform-independent controller."""

    @abstractmethod
    def available(self) -> bool:
        """Whether a notification area can be used on this machine."""

    @abstractmethod
    def run(
        self,
        on_command: Callable[[int], object],
        menu: _Menu,
        tooltip: str,
        tick: Callable[[], object] | None = None,
    ) -> int:
        """Run the native event loop until the tray is asked to exit."""

    @abstractmethod
    def request_exit(self) -> bool:
        """Ask the active native loop to return from its message pump."""

    @abstractmethod
    def notify(self, title: str, message: str) -> bool:
        """Show an informational balloon, returning whether it was accepted."""

    @abstractmethod
    def update_tooltip(self, text: str) -> bool:
        """Replace the current icon tooltip, returning whether it was accepted."""

    @abstractmethod
    def describe(self) -> str:
        """Return a one-line diagnostic description."""


class NullTray(TrayBackend):
    """Backend used when no native notification area is available."""

    def available(self) -> bool:
        return False

    def run(
        self,
        on_command: Callable[[int], object],
        menu: _Menu,
        tooltip: str,
        tick: Callable[[], object] | None = None,
    ) -> int:
        return 1

    def request_exit(self) -> bool:
        return False

    def notify(self, title: str, message: str) -> bool:
        return False

    def update_tooltip(self, text: str) -> bool:
        return False

    def describe(self) -> str:
        return f"Null tray (available: {self.available()})"


@dataclass(frozen=True, slots=True)
class _NativeTypes:
    ctypes: Any
    notify_icon_data: Any
    window_class: Any
    message: Any
    window_proc: Any


@lru_cache(maxsize=1)
def _win32_types() -> _NativeTypes:
    """Build native structures only when a Windows backend is first used."""

    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class NOTIFYICONDATA_UNION(ctypes.Union):
        _fields_ = [
            ("uTimeout", wintypes.UINT),
            ("uVersion", wintypes.UINT),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("DUMMYUNIONNAME", NOTIFYICONDATA_UNION),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    window_proc_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    window_proc = window_proc_factory(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", window_proc),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    return _NativeTypes(
        ctypes=ctypes,
        notify_icon_data=NOTIFYICONDATAW,
        window_class=WNDCLASSW,
        message=MSG,
        window_proc=window_proc,
    )


class WindowsTray(TrayBackend):
    """Native notification-area implementation with injectable DLL objects."""

    def __init__(
        self,
        icon_path: str | None = None,
        user32: Any = None,
        shell32: Any = None,
        kernel32: Any = None,
        platform: str | None = None,
    ) -> None:
        self._icon_path = str(icon_path) if icon_path is not None else None
        self._user32 = user32
        self._shell32 = shell32
        self._kernel32 = kernel32
        self._platform = sys.platform if platform is None else platform
        self._libraries_cache: tuple[Any, Any, Any] | None = None

        self._on_command: Callable[[int], object] | None = None
        self._menu: _Menu | None = None
        self._tick: Callable[[], object] | None = None
        self._tooltip = ""
        self._notify_data: Any = None
        self._window_handle: Any = None
        self._timer_id: Any = None
        self._exit_requested = False

        # RegisterClassW and CreateWindowExW retain native function pointers.
        # Both objects therefore remain strongly referenced by this instance for
        # the entire registered-class and message-loop lifetime.
        self._wndproc: Any = None
        self._wndclass: Any = None
        self._class_name = ""
        self._class_name_buffer: Any = None
        self._run_number = 0
        self._taskbar_created_message = 0

    def available(self) -> bool:
        if self._platform != "win32":
            return False
        try:
            user32, shell32, kernel32 = self._libraries()
        except (AttributeError, ImportError, OSError):
            return False
        required = (
            (user32, "RegisterClassW"),
            (user32, "RegisterWindowMessageW"),
            (user32, "CreateWindowExW"),
            (user32, "GetMessageW"),
            (user32, "TranslateMessage"),
            (user32, "DispatchMessageW"),
            (user32, "LoadIconW"),
            (user32, "SetTimer"),
            (user32, "KillTimer"),
            (user32, "DestroyWindow"),
            (user32, "UnregisterClassW"),
            (user32, "PostQuitMessage"),
            (user32, "DefWindowProcW"),
            (user32, "CreatePopupMenu"),
            (user32, "AppendMenuW"),
            (user32, "TrackPopupMenu"),
            (user32, "DestroyMenu"),
            (shell32, "Shell_NotifyIconW"),
            (kernel32, "GetModuleHandleW"),
        )
        return all(callable(getattr(dll, name, None)) for dll, name in required)

    def describe(self) -> str:
        return f"Windows tray (available: {self.available()})"

    def run(
        self,
        on_command: Callable[[int], object],
        menu: _Menu,
        tooltip: str,
        tick: Callable[[], object] | None = None,
    ) -> int:
        if not self.available():
            return 1
        self._exit_requested = False

        user32, shell32, kernel32 = self._libraries()
        types = _win32_types()
        self._configure_native_signatures(types, user32, shell32, kernel32)
        self._taskbar_created_message = int(
            user32.RegisterWindowMessageW("TaskbarCreated") or 0
        )
        self._on_command = on_command
        self._menu = menu
        self._tick = tick
        self._tooltip = str(tooltip)
        self._run_number += 1
        self._wndproc = types.window_proc(self._window_proc)
        self._class_name = (
            f"UniversalSearchTrayWindow_{id(self):x}_{self._run_number:x}"
        )

        window: Any = None
        icon: Any = None
        owns_icon = False
        timer_id: Any = None
        icon_call_attempted = False
        class_registered = False
        instance: Any = None

        try:
            instance = kernel32.GetModuleHandleW(None)
            if not instance:
                return 1
            self._wndclass = self._make_window_class(types, instance)
            class_registered = bool(
                user32.RegisterClassW(types.ctypes.byref(self._wndclass))
            )
            if not class_registered:
                return 1
            window = user32.CreateWindowExW(
                0,
                self._class_name,
                "Universal Search",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not window:
                return 1

            icon, owns_icon = self._load_icon(user32)
            if not icon:
                return 1

            self._window_handle = window
            self._notify_data = self._make_notify_data(types, window, icon, tooltip)
            icon_call_attempted = True
            if not self._add_icon(shell32, types):
                return 1

            timer_id = user32.SetTimer(window, 1, _TIMER_INTERVAL_MS, None)
            self._timer_id = timer_id
            if not timer_id:
                return 1

            message = types.message()
            while True:
                result = user32.GetMessageW(
                    types.ctypes.byref(message), None, 0, 0
                )
                if result == -1:
                    return 1
                if result == 0:
                    return 0
                user32.TranslateMessage(types.ctypes.byref(message))
                user32.DispatchMessageW(types.ctypes.byref(message))
        finally:
            if timer_id:
                self._cleanup_call(
                    "kill tray timer", user32.KillTimer, window, timer_id
                )
            if window:
                self._cleanup_call("destroy tray window", user32.DestroyWindow, window)
            if icon_call_attempted and self._notify_data is not None:
                self._cleanup_call(
                    "delete tray icon",
                    shell32.Shell_NotifyIconW,
                    _NIM_DELETE,
                    types.ctypes.byref(self._notify_data),
                )
            if owns_icon and icon:
                destroy_icon = getattr(user32, "DestroyIcon", None)
                if callable(destroy_icon):
                    self._cleanup_call("destroy tray icon", destroy_icon, icon)
                else:
                    log.warning("could not destroy tray icon: DestroyIcon is unavailable")
            if class_registered:
                self._cleanup_call(
                    "unregister tray window class",
                    user32.UnregisterClassW,
                    self._class_name,
                    instance,
                )

            self._timer_id = None
            self._notify_data = None
            self._window_handle = None
            self._on_command = None
            self._menu = None
            self._tick = None

    def _add_icon(self, shell32: Any, types: _NativeTypes) -> bool:
        data = self._notify_data
        if data is None:
            return False
        # MODIFY calls replace uFlags. Restore the full identity/message
        # flags before re-adding after Explorer publishes TaskbarCreated.
        data.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        return bool(shell32.Shell_NotifyIconW(_NIM_ADD, types.ctypes.byref(data)))

    def request_exit(self) -> bool:
        if self._window_handle is None:
            return False
        if self._exit_requested:
            return True
        try:
            user32, _, _ = self._libraries()
            result = user32.PostQuitMessage(0)
        except (AttributeError, OSError):
            log.exception("could not request tray loop exit")
            return False
        if result is not None and not bool(result):
            return False
        self._exit_requested = True
        return True

    def notify(self, title: str, message: str) -> bool:
        data = self._notify_data
        if data is None:
            return False
        types = _win32_types()
        self._write_wide_field(data, "szInfoTitle", str(title), 64)
        self._write_wide_field(data, "szInfo", str(message), 256)
        data.dwInfoFlags = _NIIF_INFO
        data.uFlags = _NIF_INFO
        _, shell32, _ = self._libraries()
        return bool(shell32.Shell_NotifyIconW(_NIM_MODIFY, types.ctypes.byref(data)))

    def update_tooltip(self, text: str) -> bool:
        data = self._notify_data
        if data is None:
            return False
        types = _win32_types()
        value = str(text)
        self._write_wide_field(data, "szTip", value, 128)
        data.uFlags = _NIF_TIP
        self._tooltip = value
        _, shell32, _ = self._libraries()
        return bool(shell32.Shell_NotifyIconW(_NIM_MODIFY, types.ctypes.byref(data)))

    def _notify_icon_data_type(self) -> Any:
        return _win32_types().notify_icon_data

    def _libraries(self) -> tuple[Any, Any, Any]:
        if self._libraries_cache is not None:
            return self._libraries_cache

        import ctypes

        user32 = self._user32
        if user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = self._shell32
        if shell32 is None:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = self._kernel32
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._libraries_cache = (user32, shell32, kernel32)
        return self._libraries_cache

    def _configure_native_signatures(
        self,
        types: _NativeTypes,
        user32: Any,
        shell32: Any,
        kernel32: Any,
    ) -> None:
        ctypes = types.ctypes
        wintypes = ctypes.wintypes
        void_pointer = ctypes.c_void_p
        signatures = (
            (user32.RegisterClassW, wintypes.USHORT, [ctypes.POINTER(types.window_class)]),
            (
                user32.RegisterWindowMessageW,
                wintypes.UINT,
                [wintypes.LPCWSTR],
            ),
            (
                user32.CreateWindowExW,
                wintypes.HWND,
                [
                    wintypes.DWORD,
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.HWND,
                    wintypes.HANDLE,
                    wintypes.HINSTANCE,
                    void_pointer,
                ],
            ),
            (
                user32.GetMessageW,
                wintypes.BOOL,
                [ctypes.POINTER(types.message), wintypes.HWND, wintypes.UINT, wintypes.UINT],
            ),
            (user32.TranslateMessage, wintypes.BOOL, [ctypes.POINTER(types.message)]),
            (
                user32.DispatchMessageW,
                ctypes.c_ssize_t,
                [ctypes.POINTER(types.message)],
            ),
            (
                user32.DefWindowProcW,
                ctypes.c_ssize_t,
                [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
            ),
            (user32.LoadIconW, wintypes.HICON, None),
            (getattr(user32, "LoadImageW", None), wintypes.HICON, None),
            (getattr(user32, "DestroyIcon", None), wintypes.BOOL, [wintypes.HICON]),
            (
                user32.SetTimer,
                wintypes.WPARAM,
                [wintypes.HWND, wintypes.WPARAM, wintypes.UINT, void_pointer],
            ),
            (user32.KillTimer, wintypes.BOOL, [wintypes.HWND, wintypes.WPARAM]),
            (user32.DestroyWindow, wintypes.BOOL, [wintypes.HWND]),
            (
                user32.UnregisterClassW,
                wintypes.BOOL,
                [wintypes.LPCWSTR, wintypes.HINSTANCE],
            ),
            (user32.PostQuitMessage, None, [wintypes.UINT]),
            (user32.CreatePopupMenu, wintypes.HMENU, []),
            (
                user32.AppendMenuW,
                wintypes.BOOL,
                [wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR],
            ),
            (getattr(user32, "GetCursorPos", None), wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)]),
            (getattr(user32, "SetForegroundWindow", None), wintypes.BOOL, [wintypes.HWND]),
            (
                user32.TrackPopupMenu,
                wintypes.UINT,
                [
                    wintypes.HMENU,
                    wintypes.UINT,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.HWND,
                    void_pointer,
                ],
            ),
            (
                getattr(user32, "PostMessageW", None),
                wintypes.BOOL,
                [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
            ),
            (user32.DestroyMenu, wintypes.BOOL, [wintypes.HMENU]),
            (
                shell32.Shell_NotifyIconW,
                wintypes.BOOL,
                [wintypes.DWORD, ctypes.POINTER(types.notify_icon_data)],
            ),
            (
                kernel32.GetModuleHandleW,
                wintypes.HMODULE,
                [wintypes.LPCWSTR],
            ),
        )
        for function, restype, argtypes in signatures:
            if not callable(function):
                continue
            try:
                function.restype = restype
                if argtypes is not None:
                    function.argtypes = argtypes
            except (AttributeError, TypeError):
                # Injected Python fakes need not expose ctypes function metadata.
                pass

    def _make_window_class(self, types: _NativeTypes, instance: Any) -> Any:
        window_class = types.window_class()
        window_class.lpfnWndProc = self._wndproc
        self._class_name_buffer = types.ctypes.c_wchar_p(self._class_name)
        window_class.lpszClassName = self._class_name_buffer
        window_class.hInstance = instance
        return window_class

    def _load_icon(self, user32: Any) -> tuple[Any, bool]:
        load_image = getattr(user32, "LoadImageW", None)
        if self._icon_path is not None and callable(load_image):
            try:
                icon = load_image(
                    None,
                    self._icon_path,
                    _IMAGE_ICON,
                    0,
                    0,
                    _LR_LOADFROMFILE | _LR_DEFAULTSIZE,
                )
            except (AttributeError, OSError):
                icon = None
            if icon:
                return icon, True
        return user32.LoadIconW(None, _IDI_APPLICATION), False

    def _make_notify_data(
        self,
        types: _NativeTypes,
        window: Any,
        icon: Any,
        tooltip: str,
    ) -> Any:
        data = types.notify_icon_data()
        data.cbSize = types.ctypes.sizeof(data)
        data.hWnd = window
        data.uID = 1
        data.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        data.uCallbackMessage = _TRAY_CALLBACK
        data.hIcon = icon
        self._write_wide_field(data, "szTip", str(tooltip), 128)
        return data

    def _write_wide_field(
        self, data: Any, field_name: str, value: str, capacity: int
    ) -> None:
        types = _win32_types()
        field = getattr(type(data), field_name)
        destination = types.ctypes.byref(data, field.offset)
        wchar_size = types.ctypes.sizeof(types.ctypes.wintypes.WCHAR)
        if wchar_size == 2:
            encoded = value.encode("utf-16-le", errors="replace")[
                : (capacity - 1) * 2
            ]
            encoded += b"\x00\x00"
            encoded += b"\x00" * (capacity * 2 - len(encoded))
            types.ctypes.memmove(destination, encoded, capacity * 2)
        else:
            # ``ctypes.wintypes.WCHAR`` is a four-byte host ``wchar_t`` on
            # Unix.  That representation is never sent to Windows; it only
            # keeps injected fake-DLL tests meaningful on non-Windows hosts.
            buffer = types.ctypes.create_unicode_buffer(value[: capacity - 1], capacity)
            types.ctypes.memmove(destination, buffer, capacity * wchar_size)

    def _window_proc(
        self,
        window: Any,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        message = int(message)
        wparam = int(wparam)
        lparam = int(lparam)
        if (
            self._taskbar_created_message
            and message == self._taskbar_created_message
        ):
            user32, shell32, _ = self._libraries()
            if not self._add_icon(shell32, _win32_types()):
                log.warning("could not restore the tray icon after Explorer restart")
            return 0
        if message == _TRAY_CALLBACK:
            if lparam in (_WM_RBUTTONUP, _WM_CONTEXTMENU):
                self._show_menu()
            elif lparam == _WM_LBUTTONDBLCLK:
                self._emit_command(_CMD_OPEN)
            return 0
        if message == _WM_COMMAND:
            self._emit_command(wparam & 0xFFFF)
            return 0
        if message == _WM_TIMER:
            if self._tick is not None:
                self._tick()
            return 0
        if message == _WM_DESTROY:
            user32, _, _ = self._libraries()
            user32.PostQuitMessage(0)
            return 0
        user32, _, _ = self._libraries()
        return int(user32.DefWindowProcW(window, message, wparam, lparam))

    def _show_menu(self) -> None:
        user32, _, _ = self._libraries()
        types = _win32_types()
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        tracked = False
        selected = 0
        try:
            source = self._menu() if callable(self._menu) else self._menu
            for item in source or ():
                if item.separator:
                    user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
                    continue
                flags = _MF_STRING
                if not item.enabled:
                    flags |= _MF_GRAYED
                user32.AppendMenuW(menu, flags, int(item.command), str(item.label))

            point = types.ctypes.wintypes.POINT(0, 0)
            get_cursor = getattr(user32, "GetCursorPos", None)
            if callable(get_cursor):
                try:
                    get_cursor(types.ctypes.byref(point))
                except OSError:
                    pass
            set_foreground = getattr(user32, "SetForegroundWindow", None)
            if callable(set_foreground):
                set_foreground(self._window_handle)
            tracked = True
            selected = int(
                user32.TrackPopupMenu(
                    menu,
                    _TPM_RIGHTBUTTON | _TPM_RETURNCMD,
                    int(point.x),
                    int(point.y),
                    0,
                    self._window_handle,
                    None,
                )
                or 0
            )
        finally:
            if tracked:
                post_message = getattr(user32, "PostMessageW", None)
                if callable(post_message):
                    try:
                        post_message(self._window_handle, _WM_NULL, 0, 0)
                    except OSError:
                        pass
            self._cleanup_call("destroy popup menu", user32.DestroyMenu, menu)
        if selected:
            self._emit_command(selected)

    def _emit_command(self, command: int) -> None:
        if self._on_command is None:
            return
        command = int(command)
        try:
            result = self._on_command(command)
        except Exception:
            log.exception("tray command handler failed")
            return
        if command == _CMD_EXIT and result is not False:
            if getattr(result, "ok", True) is not False:
                self.request_exit()

    @staticmethod
    def _cleanup_call(description: str, function: Callable[..., object], *args: object) -> None:
        try:
            function(*args)
        except Exception:
            log.exception("could not %s", description)
