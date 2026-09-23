"""No-op platform for non-Windows machines and for tests (spec 016).

Answers honestly: operations that need a shell raise
:class:`PlatformError` with the name of what was missing, and queries
return ``False``. Nothing here pretends to have opened a file, which is
what lets the core and the tests run on Linux or macOS without the code
knowing.
"""

from pathlib import Path

from universal_search.platforms.base import Platform, PlatformError


class NullPlatform(Platform):
    name = "null"

    def available(self) -> bool:
        return False

    def open_path(self, path: Path | str) -> None:
        raise PlatformError(f"no shell available to open {path}")

    def reveal(self, path: Path | str) -> None:
        raise PlatformError(f"no file manager available to reveal {path}")

    def set_autostart(self, enabled: bool, command: str | None = None) -> None:
        raise PlatformError("no startup integration available")

    def autostart_enabled(self) -> bool:
        return False

    def notify(self, title: str, message: str, *, critical: bool = False) -> bool:
        return False
