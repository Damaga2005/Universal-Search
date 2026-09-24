"""OS-backed primitives for the independent background-worker lease.

The application keeps its worker coordination files in the platform-neutral
``background`` module.  This adapter owns the two pieces that are inherently
OS-specific: an exclusive byte-range lock held for the worker lifetime and a
process identity handle that cannot drift to a reused PID before termination.
"""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


_PROCESS_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_TIMEOUT = 0x0102


class FileLease:
    """A persistent file whose first byte is locked for the object's lifetime."""

    def __init__(self, path: Path, platform: str | None = None) -> None:
        self.path = Path(path)
        self._platform = sys.platform if platform is None else platform
        self._file: BinaryIO | None = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> bool:
        if self.held:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError:
            return False

        try:
            # msvcrt can lock a byte beyond EOF, but keeping one byte in the
            # persistent sidecar also makes the held lease easy to inspect.
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


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """An OS handle that remains attached to one process generation."""

    pid: int
    handle: Any
    kind: str
    creation_id: str | None = None
    kernel32: Any = None

    def alive(self) -> bool:
        if self.kind == "windows":
            return bool(
                self.kernel32.WaitForSingleObject(self.handle, 0) == _WAIT_TIMEOUT
            )
        if self.kind == "pidfd":
            return _pidfd_alive(self.handle)
        return False

    def close(self) -> None:
        if self.handle is None:
            return
        if self.kind == "windows":
            self.kernel32.CloseHandle(self.handle)
        elif self.kind == "pidfd":
            try:
                os.close(self.handle)
            except OSError:
                pass
        object.__setattr__(self, "handle", None)
        object.__setattr__(self, "kernel32", None)

    def __enter__(self) -> "ProcessIdentity":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _pidfd_alive(descriptor: int) -> bool:
    try:
        signal.pidfd_send_signal(descriptor, 0)
    except ProcessLookupError:
        return False
    except (AttributeError, OSError):
        return False
    return True


def _windows_creation_id(kernel32: Any, handle: Any) -> str | None:
    import ctypes

    creation = ctypes.c_ulonglong()
    exited = ctypes.c_ulonglong()
    kernel = ctypes.c_ulonglong()
    user = ctypes.c_ulonglong()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    return f"{creation.value:016x}"


def _open_windows_identity(pid: int) -> ProcessIdentity | None:
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    )
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(
        _PROCESS_SYNCHRONIZE
        | _PROCESS_TERMINATE
        | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return None
    return ProcessIdentity(
        pid=pid,
        handle=handle,
        kind="windows",
        creation_id=_windows_creation_id(kernel32, handle),
        kernel32=kernel32,
    )


def open_process_identity(pid: int) -> ProcessIdentity | None:
    """Open a process-generation handle, or return ``None`` when unsupported."""

    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _open_windows_identity(pid)
    pidfd_open = getattr(os, "pidfd_open", None)
    if callable(pidfd_open) and hasattr(signal, "pidfd_send_signal"):
        try:
            descriptor = pidfd_open(pid)
        except (OSError, ProcessLookupError):
            return None
        return ProcessIdentity(pid=pid, handle=descriptor, kind="pidfd")
    return None


def current_process_creation_id() -> str | None:
    """Return a persistable creation identity when the platform exposes one."""
    identity = open_process_identity(os.getpid())
    if identity is None:
        return None
    try:
        return identity.creation_id
    finally:
        identity.close()


def terminate_process_identity(identity: ProcessIdentity | None) -> bool:
    """Terminate exactly the process represented by ``identity``."""

    if identity is None:
        return False
    if identity.kind == "windows":
        return bool(identity.kernel32.TerminateProcess(identity.handle, 1))
    if identity.kind == "pidfd":
        try:
            signal.pidfd_send_signal(identity.handle, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return False
        return True
    return False


__all__ = [
    "FileLease",
    "ProcessIdentity",
    "current_process_creation_id",
    "open_process_identity",
    "terminate_process_identity",
]
