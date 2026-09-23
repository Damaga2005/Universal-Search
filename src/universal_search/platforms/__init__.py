"""Platform adapter selection (spec 016).

    from universal_search.platforms import get_platform
    platform = get_platform()
    platform.open_path(path)

The selection happens once per process and can be replaced in tests with
:func:`set_platform`, so no test ever needs a Windows API to exercise the
code that calls the platform.
"""

import sys

from universal_search.platforms.base import Platform, PlatformError
from universal_search.platforms.null import NullPlatform
from universal_search.platforms.windows import WindowsPlatform

__all__ = [
    "NullPlatform",
    "Platform",
    "PlatformError",
    "WindowsPlatform",
    "get_platform",
    "reset_platform",
    "set_platform",
]

_current: Platform | None = None


def get_platform() -> Platform:
    """The platform for this machine, created once."""
    global _current
    if _current is None:
        _current = (
            WindowsPlatform() if sys.platform == "win32" else NullPlatform()
        )
    return _current


def set_platform(platform: Platform | None) -> None:
    """Replace the platform (tests, or a future explicit override)."""
    global _current
    _current = platform


def reset_platform() -> None:
    """Forget the selection: the next call detects the platform again."""
    set_platform(None)
