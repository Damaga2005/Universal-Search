"""Application service layer.

The desktop window talks only to this module: search execution, opening
files and configuration live here so the UI stays free of database logic
and every behaviour is testable without Tk.
"""

import logging
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from universal_search.appconfig import (
    MAX_QUERY_CHARS,
    MAX_RECENT_QUERIES,
    AppConfig,
    AppPaths,
    remember_query,
)
from universal_search.context import configured_roots, get_context
from universal_search.hotkey import (
    consume_diagnostics_request,
    consume_show_request,
    request_diagnostics,
    write_gui_pid as register_gui_pid,
    clear_gui_pid as unregister_gui_pid,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult
from universal_search.metrics import set_sink
from universal_search.organize import delete_search, load_saved, save_search
from universal_search.query import QueryError
from universal_search.gui.control_center import (
    ActionResult,
    ControlCenterService,
    ControlSnapshot,
    DerivedSnapshot,
    SourceFailure,
    SourceSnapshot,
    StorageSnapshot,
)

# Public API: everything the window (and tests) may reach through this
# module. The hotkey pid/show helpers are re-exports on purpose — the UI
# talks only to the service layer.
__all__ = [
    "ActionResult",
    "ControlCenterService",
    "ControlSnapshot",
    "DerivedSnapshot",
    "SearchService",
    "SourceFailure",
    "SourceSnapshot",
    "StorageSnapshot",
    "consume_diagnostics_request",
    "consume_show_request",
    "control_center_service",
    "indexer_status",
    "indexer_summary",
    "open_path",
    "pause_indexer",
    "register_gui_pid",
    "reveal_in_explorer",
    "request_diagnostics",
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
        self.config = AppConfig.load(self.paths)
        # Phase 026: the local semantic layer is fallback-only, so the
        # lexical engine stays authoritative and exact matches, phrases,
        # filters and operators are unchanged. With the derived tables
        # absent the hybrid engine is exactly the lexical engine.
        from universal_search.semantic import HybridSearchEngine, SemanticIndex

        lexical = SearchEngine(self.database)
        self.lexical = lexical
        self.engine: object = HybridSearchEngine(
            lexical, SemanticIndex(self.database)
        )
        self._layer_fuzzy = False
        self._apply_fuzzy()
        # Last query-language error (spec 012): the window reads this to show
        # understandable feedback instead of a traceback.
        self.last_query_error: str | None = None
        # Suggestions (phase 032), reached for the first time from the GUI in
        # phase 042. The suggester verifies every proposal against this very
        # engine and is pure: it returns a list and never touches a query.
        self._suggester: object | None = None

    def _apply_fuzzy(self) -> None:
        """Wrap the hybrid engine in the fuzzy layer, or take the layer off.

        Phase 042: the GUI now gets the phase 031 fuzzy layer, because the CLI
        has had it since 031 and its absence from the window was never a
        decision -- it was the service being wired once and never revisited.

        The layer keeps its own contract and this does not weaken it: lexical
        results are returned untouched, an explicit source or type filter
        disables it entirely, and with the derived table absent the wrapper
        *is* the lexical engine. Unwrapping goes back to the hybrid engine held
        in ``self.engine``, which is the only place the stack is assembled, so
        the two directions cannot drift apart.
        """
        from universal_search.semantic import HybridSearchEngine, SemanticIndex

        inner = HybridSearchEngine(self.lexical, SemanticIndex(self.database))
        if self.config.fuzzy_enabled:
            from universal_search.fuzzy import FuzzyIndex, FuzzySearchEngine

            self.engine = FuzzySearchEngine(inner, FuzzyIndex(self.database))
            self._layer_fuzzy = True
        else:
            self.engine = inner
            self._layer_fuzzy = False

    def set_fuzzy_enabled(self, enabled: bool) -> None:
        """Turn the fuzzy layer on or off at runtime.

        The suggester is dropped along with it: it holds a reference to the
        engine it verified against, and a stale engine would propose a
        correction this service can no longer run.
        """
        if bool(enabled) == self.config.fuzzy_enabled:
            return
        self.config = replace(self.config, fuzzy_enabled=bool(enabled))
        self._suggester = None
        self._apply_fuzzy()
        self._persist(self.config)

    def fuzzy_enabled(self) -> bool:
        return self.config.fuzzy_enabled

    @property
    def suggester(self):
        """The query suggester for this engine, built once.

        It caches the indexed vocabulary on (COUNT(*), MAX(indexed_at)), so a
        second call is free and a reindex invalidates it by itself.
        """
        if self._suggester is None:
            from universal_search.fuzzy import QuerySuggester

            self._suggester = QuerySuggester(self.engine)
        return self._suggester

    def suggest(self, query: str) -> tuple:
        """Verified corrections for ``query``, or an empty tuple.

        Every proposal is checked by *running it*: a suggestion that finds
        nothing is not offered, and a query whose tokens are already indexed
        gets nothing at all. Returns a tuple so the caller cannot mutate the
        suggester's list, and never edits the query -- that is the contract.
        """
        return tuple(self.suggester.suggest(query))

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

        Phase 042 adds :meth:`search_or_error` for callers that need the
        feedback *with* the results, and this method stays as the quiet one so
        nothing that already calls it changes behaviour.
        """
        results, _ = self.search_or_error(
            query,
            limit,
            context=context,
            explain=explain,
            source=source,
            doc_type=doc_type,
        )
        return results

    def search_or_error(
        self,
        query: str,
        limit: int = 50,
        context: str | None = None,
        explain: bool = False,
        source: str | None = None,
        doc_type: str | None = None,
    ) -> tuple[list[SearchResult], str | None]:
        """The results and the rejected-query feedback, in one call.

        Phase 042. ``search`` swallows ``QueryError`` into ``last_query_error``,
        which is right for a CLI that prints afterwards and wrong for a window:
        reading that field on the next line can pick up an error belonging to a
        *different* keystroke, because every keystroke runs on its own thread
        against the same service instance. Handing the feedback back with the
        results removes the window between the two facts.

        ``last_query_error`` is still written, because ``test_advanced_search``
        pins that part of the service's contract.
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
            return [], str(exc)
        self.last_query_error = None
        return results, None

    def related(
        self,
        document_id: str | Path | None = None,
        limit: int = 10,
        *,
        document: str | None = None,
        reference: str | None = None,
    ):
        """Return explainable related documents for an id, path or name.

        The GUI never opens SQLite or reimplements graph comparison.  The
        first request builds the optional graph if the index has not been
        derived yet; an existing graph is reused and remains independent of
        normal search ranking.
        """
        from universal_search.intelligence.graph import GraphStore

        graph = GraphStore(self.database)
        target = document if document is not None else reference
        if target is None:
            target = document_id
        if target is None:
            return []
        resolved_id = self._resolve_document_id(str(target))
        if resolved_id is None:
            return []
        if not graph.has_data():
            graph.rebuild_from_database()
        return graph.related(resolved_id, limit=limit)

    # Descriptive aliases for callers that do not want to overload ``related``.
    related_documents = related
    get_related_documents = related

    def rebuild_related_graph(self):
        """Rebuild the optional graph from canonical indexed documents."""
        from universal_search.intelligence.graph import GraphStore

        return GraphStore(self.database).rebuild_from_database()

    def _resolve_document_id(self, reference: str) -> str | None:
        """Resolve a graph reference without exposing SQL to the window."""
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT id FROM documents
                WHERE id = ? OR path = ? OR name = ?
                ORDER BY (path = ?) DESC, path
                LIMIT 1
                """,
                (reference, reference, reference, reference),
            ).fetchone()
        return str(row["id"]) if row is not None else None

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
            self.engine.database,
            [Path(root) for root in configured_roots(self.config)],
            confirm=confirm,
        )

    def record_open(self, document_id: str, query: str) -> None:
        """Record a "result opened" signal when local learning is enabled.

        Privacy: disabled by default, stored only in the local database and
        never transmitted (no network code exists in the application).
        """
        if not document_id or not self.config.usage_tracking:
            return
        try:
            # Through the lexical engine, not `self.engine`. Phase 042 found
            # this the hard way: the fuzzy layer exposes `search` and
            # `database` and nothing else, so calling `record_open` on the
            # stack raised AttributeError, was swallowed by the `except` below,
            # and local learning quietly stopped working for every user of the
            # new wiring -- with no symptom other than a log line nobody reads.
            # The signals belong to the index, so they go to the engine that
            # owns the index.
            self.lexical.record_open(document_id, query)
        except Exception:
            log.exception("could not record usage signal")

    def usage_rows(self, limit: int = 50):
        """Recorded usage signals, for diagnostics and tests."""
        return self.lexical.usage_rows(limit)

    def clear_usage(self) -> int:
        return self.lexical.clear_usage()

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

    # -- local history (phase 042) --------------------------------------------
    #
    # Four things the prompt asks for -- disable, clear, inspect, and explicit
    # retention -- and until now only the CLI had the first three, inline. They
    # are here so the window and the CLI share one implementation: two copies
    # of "delete the history" is two places for them to disagree.

    def history(self) -> tuple[str, ...]:
        """The remembered queries, newest first."""
        return self.config.recent_queries

    def history_enabled(self) -> bool:
        return self.config.recent_queries_enabled

    def set_history_enabled(self, enabled: bool) -> None:
        """Turn recording on or off.

        Turning it off stops recording and does **not** delete what is already
        there. Those are two different intentions, and a user who pauses
        recording for a week should find their history intact when they come
        back; :meth:`clear_history` is the explicit delete.
        """
        if bool(enabled) == self.config.recent_queries_enabled:
            return
        self._persist(replace(self.config, recent_queries_enabled=bool(enabled)))

    def clear_history(self) -> int:
        """Delete every remembered query. Returns how many were removed."""
        removed = len(self.config.recent_queries)
        if removed:
            self._persist(replace(self.config, recent_queries=()))
        return removed

    def history_retention(self) -> dict[str, object]:
        """The retention rules, as data, so the UI can state them.

        Phase 042 asks for *explicit* retention semantics. They were explicit in
        code -- a cap and a length limit -- and implicit everywhere else,
        because nothing ever said so. Stating them is the whole point, and it
        stays in step with the policy because both numbers are imported, not
        copied.

        There is no time-based expiry: entries carry no timestamp, so a TTL
        would be a schema change with a migration, not a setting. The honest
        description is the count cap, the length cap, and where it all lives.
        """
        return {
            "enabled": self.config.recent_queries_enabled,
            "kept": len(self.config.recent_queries),
            "max_entries": MAX_RECENT_QUERIES,
            "max_chars": MAX_QUERY_CHARS,
            "file": str(self.paths.config_file),
            "transmitted": False,
        }

    # -- saved searches (phase 036 data, phase 042 surface) -------------------

    def saved_searches(self) -> tuple:
        """The saved searches, normalised and validated."""
        return load_saved(self.config.saved_searches)

    def store_saved_search(self, search) -> tuple:
        """Persist an already-built :class:`SavedSearch`."""
        updated = save_search(self.config.saved_searches, search)
        self._persist(replace(self.config, saved_searches=updated))
        return updated

    def delete_saved_search(self, name: str) -> bool:
        updated = delete_search(self.config.saved_searches, name)
        if updated == self.config.saved_searches:
            return False
        self._persist(replace(self.config, saved_searches=updated))
        return True

    def _persist(self, config: AppConfig) -> None:
        """Save, and log rather than crash if the disk says no."""
        try:
            self.save_config(config)
        except Exception:
            log.exception("could not persist configuration")

    def control_center(self) -> ControlCenterService:
        """Return the operational service backed by this search service."""
        return ControlCenterService(service=self)

    def control_snapshot(self) -> ControlSnapshot:
        """Read the current control-center state without constructing Tk."""
        return self.control_center().snapshot()


def control_center_service(
    paths: AppPaths | None = None,
    database_path: Path | str | None = None,
) -> ControlCenterService:
    """Construct the operational service without involving Tk."""
    return ControlCenterService(paths=paths, database_path=database_path)


def open_path(path: Path | str) -> None:
    """Open a file with the default application, through the platform.

    All Windows behaviour lives in the adapter (spec 016): this function
    only decides *what* to open, never *how*.
    """
    target = str(path)
    log.info("opening %s", target)
    from universal_search.platforms import get_platform

    get_platform().open_path(target)


def reveal_in_explorer(path: Path | str) -> None:
    """Reveal the file in the platform's file manager."""
    target = str(path)
    log.info("revealing %s", target)
    from universal_search.platforms import get_platform

    get_platform().reveal(target)


def notify(title: str, message: str, *, critical: bool = False) -> bool:
    """Show a native message, only for messages worth interrupting for."""
    from universal_search.platforms import get_platform

    return get_platform().notify(title, message, critical=critical)


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
