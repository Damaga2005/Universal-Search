"""The platform seam (spec 016).

Everything Windows-specific lives behind :class:`Platform`, so the core
(domain, extractors, index, ranking, search) never imports ``ctypes`` or
``winreg`` and the test suite runs anywhere.

Two implementations ship:

* :class:`~universal_search.platforms.windows.WindowsPlatform` — the real
  one, with every OS call injectable so it can be tested on any machine.
* :class:`~universal_search.platforms.null.NullPlatform` — a no-op that
  answers honestly ("not supported here") instead of pretending.

The GUI and the background worker depend on this interface, never on a
concrete platform: that is what keeps "the core is platform-independent"
a property of the code rather than a promise in a document.
"""

from pathlib import Path


class PlatformError(RuntimeError):
    """A platform operation is unavailable or failed in a way we can name."""


class Platform:
    """Operations the shell may or may not be able to perform."""

    name = "abstract"

    def available(self) -> bool:
        """True when this platform can perform the operations below."""
        raise NotImplementedError

    def open_path(self, path: Path | str) -> None:
        """Open a file or folder with the default handler."""
        raise NotImplementedError

    def reveal(self, path: Path | str) -> None:
        """Show a file inside its parent folder (Explorer on Windows)."""
        raise NotImplementedError

    def set_autostart(self, enabled: bool, command: str | None = None) -> None:
        """Register or unregister the application to start with Windows."""
        raise NotImplementedError

    def autostart_enabled(self) -> bool:
        """True when the application is registered to start with Windows."""
        raise NotImplementedError

    def notify(self, title: str, message: str, *, critical: bool = False) -> bool:
        """Tell the user something. False when nothing was shown.

        Only ``critical=True`` may interrupt: a search box that pops a
        dialog for ordinary information is a nuisance, not a feature.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """One line for diagnostics."""
        return f"{self.name} (available: {self.available()})"
