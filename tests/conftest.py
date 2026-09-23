import sqlite3
from pathlib import Path

import pytest

# -- Tk availability -----------------------------------------------------------
# The GUI tests create a real Tk root. On a loaded Windows machine the
# interpreter's own Tcl/Tk library files are occasionally unreadable for a
# moment ("couldn't read file ... ttk/utils.tcl"), which surfaces as a
# TclError before any application code runs. That is a fact about the
# machine, not a defect in the window, so the tests that need a window
# skip with the reason instead of erroring. One probe per session: the
# pure tests in the same modules keep running either way.

_TK_REASON: list[str | None] = []


def _probe_tk() -> str | None:
    if not _TK_REASON:
        try:
            import tkinter

            root = tkinter.Tk()
        except Exception as exc:  # TclError on a missing/unreadable library
            _TK_REASON.append(f"{type(exc).__name__}: {exc}")
        else:
            _TK_REASON.append(None)
            root.withdraw()
            root.destroy()
    return _TK_REASON[0]


@pytest.fixture(scope="session")
def tk_guard() -> None:
    """Skip the calling test when a Tk window cannot be created here."""
    reason = _probe_tk()
    if reason:
        pytest.skip(f"Tk runtime unavailable on this machine: {reason}")


def count_rows(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


@pytest.fixture
def row_count():
    return count_rows
