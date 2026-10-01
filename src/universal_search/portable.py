"""Portable mode: keep every byte next to the executable (phase 037).

An installed copy keeps its data in ``%LOCALAPPDATA%\\Universal Search``, which
is the right default: uninstalling must not leave the user's index behind, and
two accounts on one PC must not share an index.

But that default makes the application unusable in the three situations where
a portable copy is the *only* sensible thing:

* a USB stick that has to carry the index with it, so it works on another PC;
* a corporate machine where nothing may be written outside a network share;
* a read-only or locked-down environment where ``%LOCALAPPDATA%`` is managed
  by someone else and cannot be trusted.

Portable mode is therefore a real deployment mode, not a trick: when it is on,
the index, the configuration, the logs and every coordination file live in a
``UniversalSearch-data`` directory beside the executable, and **nothing at all
is written under ``%LOCALAPPDATA%``**.

Three ways to ask for it, in the order they are honoured:

1. ``UNIVERSAL_SEARCH_PORTABLE=1`` — for a launcher script, a CI job or a
   single command.
2. A ``portable.txt`` marker file next to the executable — the switch that
   survives being copied on a stick, and the only one that does not depend on
   the environment of whoever launches it.
3. ``universal-search portable on`` — writes that marker.

An explicit ``UNIVERSAL_SEARCH_HOME`` always wins over all three, because the
background worker passes it to its child process and a test that sets it means
exactly what it says. Portable mode is a *default*, and a default that
overrides an explicit instruction is not a default.

Two rules this module exists to keep:

* **Portable never silently falls back.** If the portable directory cannot be
  created or written, the application says so and stops. Falling back to
  ``%LOCALAPPDATA%`` would mean an index the user cannot find, cannot delete
  and does not know about — the exact failure this mode exists to prevent.
* **Portable is visible.** :func:`describe` is what ``privacy show`` and the
  diagnostics print, so the location of the data is never a guess.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Environment switches. Both are read on every call (never cached), so a test
# or a launcher can change them mid-process and get an honest answer.
HOME_ENV = "UNIVERSAL_SEARCH_HOME"
PORTABLE_ENV = "UNIVERSAL_SEARCH_PORTABLE"

# Marker file next to the executable. Named with an extension so it is obvious
# what it is on a stick full of documents, and prefixed so a glob for ``*.txt``
# in that folder is not ambiguous about it.
PORTABLE_MARKER = "portable.txt"

# Where portable data goes. A directory, not the executable's own folder: the
# index, the logs and the lock files are churn, and a folder whose contents
# change every second is unpleasant to copy onto another PC.
PORTABLE_DATA_DIRNAME = "UniversalSearch-data"

# Marker contents are ignored; only its presence matters. Kept short so a
# curious user can open it in Notepad and understand what it does.
MARKER_TEXT = (
    "Universal Search runs in portable mode: the index, the configuration and\n"
    "the logs live in the 'UniversalSearch-data' folder next to this file.\n"
    "Delete this file to go back to %LOCALAPPDATA%.\n"
)


class PortableUnavailable(RuntimeError):
    """Portable mode was requested but its directory cannot be used.

    Raised instead of falling back to the per-user directory: a portable copy
    that quietly wrote elsewhere would be worse than one that refuses to run.
    """


@dataclass(frozen=True, slots=True)
class PortableStatus:
    """Where the data lives, and why."""

    portable: bool
    home: Path
    reason: str
    marker: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "portable": self.portable,
            "home": str(self.home),
            "reason": self.reason,
            "marker": str(self.marker) if self.marker is not None else None,
        }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_frozen() -> bool:
    """True inside a PyInstaller build, whatever its mode."""
    return bool(getattr(sys, "frozen", False))


def executable_directory() -> Path:
    """Folder that holds the running program.

    For a frozen build that is the folder of the executable — including a
    one-file build, where PyInstaller extracts the runtime elsewhere and only
    ``sys.executable`` still points at the user's copy. From source it is the
    current directory: a ``python -m universal_search.cli`` run has no
    meaningful "installation", and writing next to the interpreter would need
    administrator rights for no benefit.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def marker_path(base: Path | None = None) -> Path:
    """Where the marker would live for ``base``."""
    return (base or executable_directory()) / PORTABLE_MARKER


def requested() -> tuple[bool, str]:
    """Is portable mode asked for, and by what? ``(False, reason)`` by default."""
    if _truthy(os.environ.get(PORTABLE_ENV)):
        return True, f"{PORTABLE_ENV}=1"
    marker = marker_path()
    if marker.is_file():
        return True, f"marcador {marker.name} junto al ejecutable"
    return False, "sin marcador y sin variable de entorno"


def status(home: Path | None = None) -> PortableStatus:
    """Resolve where the data lives and say which rule decided it."""
    override = os.environ.get(HOME_ENV)
    if override:
        return PortableStatus(
            False, Path(override), f"{HOME_ENV} manda sobre todo lo demás"
        )
    wanted, reason = requested()
    base = executable_directory()
    marker = marker_path(base)
    if not wanted:
        # ``home`` is the caller's answer for the normal case; it is passed in
        # so this function stays a pure description of *this* decision and not
        # a second, subtly different copy of ``default_home``.
        from universal_search.appconfig import per_user_home

        return PortableStatus(False, home or per_user_home(), reason, marker)
    return PortableStatus(True, base / PORTABLE_DATA_DIRNAME, reason, marker)


def resolve() -> Path:
    """The application home, honouring every rule in :func:`status`."""
    return status().home


def ensure_writable(directory: Path) -> None:
    """Prove the directory can really be used, or say why it cannot.

    A portable build on a locked-down PC often fails at exactly this point, and
    the failure a user can act on is "this folder is read-only", not a
    ``PermissionError`` three frames deep.
    """
    probe = directory / ".portable-write-test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise PortableUnavailable(
            f"no se puede escribir en {directory}: {exc.strerror or exc}. "
            "El modo portable no cae a %LOCALAPPDATA%: un índice en un sitio "
            "que el usuario no sabe es peor que no arrancar."
        ) from exc


def enable(base: Path | None = None) -> Path:
    """Write the marker and return the portable data directory."""
    target = base or executable_directory()
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / PORTABLE_MARKER).write_text(MARKER_TEXT, encoding="utf-8")
    except OSError as exc:
        raise PortableUnavailable(
            f"no se pudo escribir el marcador en {target}: "
            f"{exc.strerror or exc}"
        ) from exc
    return target / PORTABLE_DATA_DIRNAME


def disable(base: Path | None = None) -> bool:
    """Remove the marker. True when there was one."""
    target = marker_path(base)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PortableUnavailable(
            f"no se pudo borrar {target}: {exc.strerror or exc}"
        ) from exc
    return True


def describe() -> str:
    """One human-readable line for the CLI and the diagnostics."""
    current = status()
    if not current.portable:
        return (
            f"modo normal (no portable): los datos están en {current.home}"
        )
    return (
        f"modo portable: los datos están en {current.home} "
        f"({current.reason})"
    )