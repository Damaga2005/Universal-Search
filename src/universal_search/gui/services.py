"""Application service layer.

The desktop window talks only to this module: search execution, opening
files and configuration live here so the UI stays free of database logic
and every behaviour is testable without Tk.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult

log = logging.getLogger("universal_search.services")


class SearchService:
    """Owns the search core used by the desktop window."""

    def __init__(
        self,
        database_path: Path | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        self.paths = paths or AppPaths.discover()
        self.paths.ensure()
        self.database = SearchDatabase(
            Path(database_path) if database_path else self.paths.database
        )
        self.engine = SearchEngine(self.database)
        self.config = AppConfig.load(self.paths)

    def search(self, query: str, limit: int = 50) -> list[SearchResult]:
        return self.engine.search(query, limit)

    def reload_config(self) -> AppConfig:
        self.config = AppConfig.load(self.paths)
        return self.config

    def save_config(self, config: AppConfig) -> None:
        config.save(self.paths)
        self.config = config


def open_path(path: Path | str) -> None:
    """Open a file with the default Windows application."""
    target = str(path)
    log.info("opening %s", target)
    if hasattr(os, "startfile"):
        os.startfile(target)  # noqa: S606 — deliberate shell open on Windows
        return
    raise RuntimeError(f"no handler to open {target} on this platform")


def reveal_in_explorer(path: Path | str) -> None:
    """Reveal the file in Windows Explorer (does not wait for the window)."""
    target = str(path)
    log.info("revealing %s", target)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", target])
        return
    raise RuntimeError(f"explorer reveal is only supported on Windows: {target}")
