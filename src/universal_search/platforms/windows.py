"""Windows implementation of the platform seam (spec 016).

Every OS touchpoint is injectable — ``startfile``, ``popen``, the registry
module and the ``user32`` object — so the whole adapter is testable on a
machine that is not Windows, and on one that is.

Documented limitations, stated plainly:

* **Notifications are modal, not toasts.** A real toast needs a registered
  AppUserModelID and a COM/WinRT dependency; neither belongs in an
  application that otherwise needs nothing beyond ``pypdf`` and
  ``watchdog``. ``notify`` therefore shows a native message box *only* for
  critical messages, and returns False otherwise. The window and the CLI
  use it for exactly one case today: the global hotkey could not be
  registered because another application owns it.
* **Start Menu and Explorer integration are installed by the packaging
  scripts** (``packaging/make-start-menu.ps1``,
  ``packaging/explorer-search.ps1``), not at runtime: writing registry
  keys or shortcuts behind the user's back is not something an app should
  do on every launch.
* **The Windows Search protocol is not integrated.** Universal Search
  cannot act as a Windows Search provider; if that is ever attempted it
  belongs in an adapter exactly like this one, with its limitations
  documented rather than silently absent.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from universal_search.platforms.base import Platform, PlatformError

log = logging.getLogger("universal_search.platforms.windows")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Exactly the value name shipped in phase 006: renaming it would orphan
# the entry already present in users' Run keys.
AUTOSTART_NAME = "Universal Search"

MB_OK = 0x00000000
MB_ICONWARNING = 0x00000030
MB_ICONERROR = 0x00000010


class WindowsPlatform(Platform):
    """Shell integration on Windows, with injectable OS touchpoints."""

    name = "windows"

    def __init__(
        self,
        *,
        startfile=None,
        popen=None,
        registry=None,
        user32=None,
        platform: str | None = None,
    ) -> None:
        self._startfile = startfile
        self._popen = popen
        self._registry = registry
        self._user32 = user32
        self._platform = platform if platform is not None else sys.platform

    # -- capability ------------------------------------------------------------

    def available(self) -> bool:
        return self._platform == "win32"

    def _require(self, operation: str) -> None:
        if not self.available():
            raise PlatformError(f"{operation} is only available on Windows")

    # -- files and folders -----------------------------------------------------

    def open_path(self, path: Path | str) -> None:
        """Open with the default application; missing files raise."""
        self._require("open")
        target = Path(path)
        if not target.exists():
            raise PlatformError(f"cannot open {target}: it does not exist")
        opener = self._startfile or getattr(os, "startfile", None)
        if opener is None:
            raise PlatformError("no shell opener available")
        try:
            opener(str(target))
        except OSError as exc:
            # A file with no associated application, a locked file or a
            # deleted target all arrive here: name it, never traceback it.
            raise PlatformError(f"could not open {target}: {exc}") from exc

    def reveal(self, path: Path | str) -> None:
        """Select the file in Explorer without waiting for the window."""
        self._require("reveal")
        target = Path(path)
        if not target.exists():
            raise PlatformError(f"cannot reveal {target}: it does not exist")
        spawn = self._popen or subprocess.Popen
        try:
            spawn(["explorer", "/select,", str(target)])
        except OSError as exc:
            raise PlatformError(f"could not reveal {target}: {exc}") from exc

    # -- startup ---------------------------------------------------------------

    def set_autostart(self, enabled: bool, command: str | None = None) -> None:
        self._require("autostart")
        set_autostart(enabled, command=command, registry=self._registry)

    def autostart_enabled(self) -> bool:
        self._require("autostart")
        return autostart_enabled(registry=self._registry)

    # -- notifications ---------------------------------------------------------

    def notify(self, title: str, message: str, *, critical: bool = False) -> bool:
        """Native message box for critical messages only (see module docs)."""
        if not critical or not self.available():
            return False
        user32 = self._user32
        if user32 is None:
            try:  # pragma: no cover - exercised on Windows only
                import ctypes

                user32 = ctypes.windll.user32
            except Exception:  # pragma: no cover - no desktop session
                return False
        icon = MB_ICONERROR if critical else MB_ICONWARNING
        try:  # pragma: no cover - exercised on Windows only
            user32.MessageBoxW(None, str(message), str(title), icon | MB_OK)
        except Exception:
            log.exception("could not show a message box")
            return False
        return True


def _autostart_command() -> str:
    """Command registered in the Run key, reusing the worker's own value."""
    from universal_search.background import autostart_command

    return autostart_command()


def _winreg(registry=None):
    """The injected registry, or the real ``winreg`` on Windows."""
    if registry is not None:
        return registry
    import winreg  # imported lazily: only Windows has it

    return winreg


def set_autostart(
    enabled: bool, *, command: str | None = None, registry=None
) -> None:
    """Create or remove the ``Run`` entry that starts the worker.

    Module-level (not only a method) so the background worker can call it
    with an injected registry on any platform: the behaviour lives in the
    adapter, the caller only chooses the registry.
    """
    registry = _winreg(registry)
    if command is None:
        command = _autostart_command()
    try:
        key = registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            registry.KEY_SET_VALUE | registry.KEY_QUERY_VALUE,
        )
    except FileNotFoundError:
        key = registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY)
    try:
        if enabled:
            registry.SetValueEx(key, AUTOSTART_NAME, 0, registry.REG_SZ, command)
        else:
            try:
                registry.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass  # already absent: the desired state, not an error
    except OSError as exc:
        raise PlatformError(f"could not change the startup entry: {exc}") from exc
    finally:
        registry.CloseKey(key)


def autostart_enabled(registry=None) -> bool:
    """True when the ``Run`` entry exists."""
    registry = _winreg(registry)
    try:
        key = registry.OpenKey(
            registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_QUERY_VALUE
        )
    except FileNotFoundError:
        return False
    try:
        registry.QueryValueEx(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return False
    finally:
        registry.CloseKey(key)
