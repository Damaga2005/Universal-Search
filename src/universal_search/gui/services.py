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

from universal_search.appconfig import AppConfig, AppPaths, remember_query
from universal_search.context import get_context
from universal_search.hotkey import (
    consume_show_request,
    write_gui_pid as register_gui_pid,
    clear_gui_pid as unregister_gui_pid,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult
from universal_search.metrics import set_sink
from universal_search.query import QueryError

# Public API: everything the window (and tests) may reach through this
# module. The hotkey pid/show helpers are re-exports on purpose — the UI
# talks only to the service layer.
__all__ = [
    "SearchService",
    "consume_show_request",
    "indexer_status",
    "indexer_summary",
    "open_path",
    "pause_indexer",
    "register_gui_pid",
    "reveal_in_explorer",
    "resume_indexer",
    "set_autostart",
    "start_indexer",
    "stop_indexer",
    "unregister_gui_pid",
]

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
        # Local metrics (spec 011): searches record latency/result counts
        # and flush here, throttled, into the app's metrics.jsonl.
        set_sink(self.paths.metrics_file)
        self.database = SearchDatabase(
            Path(database_path) if database_path else self.paths.database
        )
        self.engine = SearchEngine(self.database)
        self.config = AppConfig.load(self.paths)
        # Last query-language error (spec 012): the window reads this to show
        # understandable feedback instead of a traceback.
        self.last_query_error: str | None = None

    def search(
        self,
        query: str,
        limit: int = 50,
        context: str | None = None,
        explain: bool = False,
        source: str | None = None,
        doc_type: str | None = None,
    ) -> list[SearchResult]:
        """Search with the active (or explicitly named) personal context.

        Context resolution, usage-learning gating and every ranking decision
        live in the core; this method only supplies configuration.
        ``source``/``doc_type`` are index-level filters (spec 009): queries
        never scan the filesystem.

        A malformed query is feedback, not a crash (spec 012): ``QueryError``
        is captured in ``last_query_error`` and resolves to no results.
        """
        name = context if context is not None else self.config.active_context
        resolved = get_context(self.config, name) if name else None
        try:
            results = self.engine.search(
                query,
                limit,
                context=resolved,
                usage=self.config.usage_tracking,
                explain=explain,
                source=source,
                doc_type=doc_type,
            )
        except QueryError as exc:
            self.last_query_error = str(exc)
            return []
        self.last_query_error = None
        return results

    def diagnostics(self) -> dict:
        """Index statistics and health, assembled without Tk (spec 015).

        Read-only: this is the data behind the window's diagnostics view,
        and it is testable precisely because no widget is involved.
        """
        from universal_search.diagnostics import check, collect

        database = self.engine.database
        statistics = collect(database, self.paths)
        report = check(database, self.paths)
        return {"summary": statistics.as_dict(), "health": report.as_dict()}

    def diagnostics_report(self) -> str:
        """The same information as plain text, for the window or a log."""
        from universal_search.diagnostics import check, collect

        database = self.engine.database
        statistics = collect(database, self.paths)
        report = check(database, self.paths)
        return "\n".join([
            f"documents: {statistics.documents}"
            f" ({statistics.with_content} with text)",
            f"size:      {statistics.total_bytes} bytes",
            f"schema:    {statistics.schema_version}"
            f" (app {statistics.app_version})",
            f"derived:   {statistics.intelligence_rows} analysed",
            f"worker:    {statistics.worker_state or 'not running'}",
            "",
            report.render(),
        ])

    def rebuild_index(self, *, confirm: bool = False):
        """Delete and reindex everything from the configured roots (015).

        Destructive: requires ``confirm=True``, and the window asks the
        user before passing it.
        """
        from universal_search.diagnostics import rebuild_all

        return rebuild_all(
            self.engine.database, list(self.config.roots), confirm=confirm
        )

    def record_open(self, document_id: str, query: str) -> None:
        """Record a "result opened" signal when local learning is enabled.

        Privacy: disabled by default, stored only in the local database and
        never transmitted (no network code exists in the application).
        """
        if not document_id or not self.config.usage_tracking:
            return
        try:
            self.engine.record_open(document_id, query)
        except Exception:
            log.exception("could not record usage signal")

    def record_query(self, query: str) -> None:
        """Remember an executed query for the recents menu.

        Optional (``recent_queries_enabled``, default on), local-only, and
        capped by the shared :func:`remember_query` policy.
        """
        updated = remember_query(self.config, query)
        if updated is self.config:
            return
        try:
            self.save_config(updated)
        except Exception:
            log.exception("could not persist recent queries")

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


# -- background indexer control (thin wrappers over the background module) -------

def indexer_status(paths: AppPaths | None = None) -> dict | None:
    from universal_search import background

    return background.read_status(paths or AppPaths.discover())


def indexer_summary(paths: AppPaths | None = None) -> str:
    """Short human-readable indexer state for the status bar."""
    from universal_search import background

    paths = paths or AppPaths.discover()
    pid = background.read_lock_pid(paths)
    running = pid is not None and background.process_alive(pid)
    status = background.read_status(paths)
    if not running and status is None:
        return "indexador: detenido"
    state = (status or {}).get("state", "starting")
    labels = {
        "idle": "en reposo",
        "indexing": "indexando",
        "paused": "en pausa",
        "error": "error",
    }
    label = labels.get(state, state)
    if background.is_paused(paths) and state != "paused":
        label = "en pausa"
    suffix = f" · pid {pid}" if running else ""
    return f"indexador: {label}{suffix}"


def start_indexer(paths: AppPaths | None = None) -> str:
    from universal_search import background

    _state, message = background.start(paths)
    return message


def stop_indexer(paths: AppPaths | None = None) -> str:
    from universal_search import background

    _state, message = background.stop(paths)
    return message


def pause_indexer(paths: AppPaths | None = None) -> str:
    from universal_search import background

    background.pause(paths or AppPaths.discover())
    return "indexación pausada"


def resume_indexer(paths: AppPaths | None = None) -> str:
    from universal_search import background

    background.resume(paths or AppPaths.discover())
    return "indexación reanudada"


def set_autostart(enabled: bool) -> str:
    from universal_search import background

    background.set_autostart(enabled)
    return (
        "se iniciará con Windows" if enabled else "ya no se inicia con Windows"
    )
