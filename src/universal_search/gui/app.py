"""Universal Search desktop window.

Pure view: widgets, key bindings and rendering only. Every operation goes
through :mod:`universal_search.gui.services`, so the core stays independent
of the GUI and the window can be exercised in tests.
"""

import logging
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from universal_search import __version__
from universal_search.context import load_contexts
from universal_search.gui import rows, services, strings, theme as theme_module
from universal_search.gui.batch import BatchOperations
from universal_search.gui.services import (
    SearchService,
    open_path,
    reveal_in_explorer,
)
from universal_search.index.search import SearchResult
from universal_search.query import QueryError

log = logging.getLogger("universal_search.gui")

DEBOUNCE_MS = 150
# How often the main loop collects finished searches. Short enough to feel
# instant, long enough that an idle window does no work.
RESULT_POLL_MS = 40
DEFAULT_LIMIT = 50
SHOW_POLL_MS = 250
SOURCE_FILTER_VALUES = ("(todas)", "local", "onedrive", "network", "removable")
TYPE_FILTER_VALUES = ("(todos)", "pdf", "docx", "xlsx", "pptx", "md", "txt")

TYPE_LABELS = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".xlsx": "Excel",
    ".pptx": "PowerPoint",
    ".md": "Markdown",
    ".txt": "Texto",
}


class SearchWindow(tk.Tk):
    def __init__(self, service: SearchService | None = None) -> None:
        super().__init__()
        self.service = service or SearchService()
        self.results: list[SearchResult] = []
        self._search_job: str | None = None
        self._result_poll: str | None = None
        self._results_queue: queue.Queue = queue.Queue()
        self._related_queue: queue.Queue = queue.Queue()
        self._generation = 0
        self._related_generation = 0
        self._inflight = 0
        self._related_inflight = 0
        self._related_poll: str | None = None
        self.related_window: tk.Toplevel | None = None
        self.control_center_window: object | None = None
        self.closed = False

        # Theme and scaling resolved once, from configuration (spec 017).
        config = self.service.config
        self.theme = theme_module.resolve(
            getattr(config, "theme", "system")
        )
        self.ui_scale = theme_module.clamp_scale(
            getattr(config, "ui_scale", 1.0)
        )
        self.fonts = theme_module.fonts(self.ui_scale)
        self.configure(background=self.theme.background)

        self.title(f"Universal Search {__version__}")
        self.geometry(config.window_geometry or "940x580")
        self.minsize(680, 400)

        self._build_ui()
        self._declare_accessible_names()
        self._bind_keys()
        # Publish the PID: the worker's global hotkey targets this window.
        services.register_gui_pid(self.service.paths)
        self._set_status(strings.get("SEARCH.READY"))
        self.query_var.trace_add("write", self._on_query_changed)
        self.entry.focus_set()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        top = ttk.Frame(self, padding=(12, 12, 12, 6))
        top.pack(fill="x")
        self.query_var = tk.StringVar()
        self.entry = ttk.Entry(
            top,
            textvariable=self.query_var,
            font=self.fonts["entry"],
            takefocus=True,  # explicit: never rely on a style default
        )
        self.entry.pack(fill="x")

        context_bar = ttk.Frame(top, padding=(0, 6, 0, 0))
        context_bar.pack(fill="x")
        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.CONTEXT")).pack(side="left")
        self.context_var = tk.StringVar()
        self.context_combo = ttk.Combobox(
            context_bar,
            textvariable=self.context_var,
            state="readonly",
            width=28,
            values=self._context_values(),
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.context_combo.pack(side="left", padx=(6, 0))
        self.context_combo.bind("<<ComboboxSelected>>", self._on_context_changed)
        self.context_var.set(self.service.config.active_context or "(todos)")

        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.SOURCE")).pack(side="left", padx=(12, 0))
        self.source_var = tk.StringVar(value=SOURCE_FILTER_VALUES[0])
        self.source_combo = ttk.Combobox(
            context_bar,
            textvariable=self.source_var,
            state="readonly",
            width=9,
            values=SOURCE_FILTER_VALUES,
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.source_combo.pack(side="left", padx=(6, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.TYPE")).pack(side="left", padx=(12, 0))
        self.type_var = tk.StringVar(value=TYPE_FILTER_VALUES[0])
        self.type_combo = ttk.Combobox(
            context_bar,
            textvariable=self.type_var,
            state="readonly",
            width=8,
            values=TYPE_FILTER_VALUES,
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.type_combo.pack(side="left", padx=(6, 0))
        self.type_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        self.recent_button = ttk.Menubutton(
            context_bar,
            text=strings.get("SEARCH.RECENTS"),
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.recent_menu = tk.Menu(self.recent_button, tearoff=0)
        self.recent_button["menu"] = self.recent_menu
        self.recent_button.pack(side="right")
        self._refresh_recent_menu()

        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(
            label=strings.get("MENU.FILE.COPY_PATH"), command=self._copy_path
        )
        file_menu.add_separator()
        file_menu.add_command(label=strings.get("MENU.FILE.EXIT"), command=self._on_close)
        menu.add_cascade(label=strings.get("MENU.FILE"), menu=file_menu)

        # Phase 035: batch operations over a multi-selection.
        batch_menu = tk.Menu(menu, tearoff=0)
        batch_menu.add_command(
            label=strings.get("MENU.SELECTION.OPEN"), command=self._open_selected
        )
        batch_menu.add_command(
            label=strings.get("MENU.SELECTION.REVEAL"),
            command=self._reveal_selected,
        )
        batch_menu.add_command(
            label=strings.get("MENU.SELECTION.COPY"),
            command=self._copy_selected_paths,
        )
        batch_menu.add_separator()
        batch_menu.add_command(
            label=strings.get("MENU.SELECTION.FORGET"),
            command=self._forget_selected,
        )
        menu.add_cascade(label=strings.get("MENU.SELECTION"), menu=batch_menu)

        self.indexer_menu = tk.Menu(menu, tearoff=0)
        self.indexer_menu.add_command(
            label=strings.get("MENU.INDEXER.START"), command=lambda: self._indexer_action("start")
        )
        self.indexer_menu.add_command(
            label=strings.get("MENU.INDEXER.STOP"), command=lambda: self._indexer_action("stop")
        )
        self.indexer_menu.add_command(
            label=strings.get("MENU.INDEXER.PAUSE"), command=lambda: self._indexer_action("pause")
        )
        self.indexer_menu.add_command(
            label=strings.get("MENU.INDEXER.RESUME"), command=lambda: self._indexer_action("resume")
        )
        self.indexer_menu.add_separator()
        self.autostart_var = tk.BooleanVar(
            value=self.service.config.start_with_windows
        )
        self.indexer_menu.add_checkbutton(
            label=strings.get("MENU.INDEXER.AUTOSTART"),
            variable=self.autostart_var,
            command=self._toggle_autostart,
        )
        menu.add_cascade(label=strings.get("MENU.INDEXER"), menu=self.indexer_menu)

        diagnose_menu = tk.Menu(menu, tearoff=0)
        diagnose_menu.add_command(
            label=strings.get("MENU.DIAGNOSE.SUMMARY"), command=self._show_diagnostics
        )
        diagnose_menu.add_command(
            label=strings.get("MENU.DIAGNOSE.CONTROL_CENTER"), command=self._show_control_center
        )
        diagnose_menu.add_command(
            label=strings.get("MENU.DIAGNOSE.RELATED"), command=self._show_related
        )
        menu.add_cascade(label=strings.get("MENU.DIAGNOSE"), menu=diagnose_menu)
        self.config(menu=menu)

        middle = ttk.Frame(self, padding=(12, 0, 12, 0))
        middle.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            middle,
            font=self.fonts["body"],
            activestyle="none",
            exportselection=False,
            # Phase 035: a selection of many results, so they can be opened,
            # revealed or copied in one action. Ctrl+click adds, Shift+click
            # extends; the keyboard focus ring behaviour is unchanged.
            selectmode="extended",
            relief="flat",
            takefocus=True,  # reachable with Tab, visible with the focus ring
            highlightthickness=1,
            highlightcolor=self.theme.accent,
            highlightbackground=self.theme.background,
            background=self.theme.background,
            foreground=self.theme.foreground,
            selectbackground=self.theme.selection_background,
            selectforeground=self.theme.selection_foreground,
        )
        scrollbar = ttk.Scrollbar(middle, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        statusbar = ttk.Frame(self, padding=(12, 4))
        statusbar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar()
        # Kept as a real widget, not a temporary: phase 039 found that `danger`
        # and `busy` were declared in both palettes and drawn nowhere, so a
        # failure looked exactly like an ordinary status line. Severity is a
        # property of the text's importance, not of how loudly it failed.
        self.status_label = ttk.Label(
            statusbar, textvariable=self.status_var, foreground=self.theme.muted
        )
        self.status_label.pack(side="left")
        self.indexer_var = tk.StringVar(value="indexador: …")
        ttk.Label(
            statusbar, textvariable=self.indexer_var, foreground=self.theme.muted
        ).pack(side="right")

        self.preview = ttk.Label(
            self, text="", padding=(12, 8), justify="left",
            font=self.fonts["body"], foreground=self.theme.muted,
        )
        self.preview.pack(fill="x", side="bottom")

    def _declare_accessible_names(self) -> None:
        """Name every interactive control (phase 039).

        Tk cannot say which ``Label`` belongs to which control: three labels
        share one parent frame here, so anything positional would announce
        "Contexto:" for three different filters. The association is declared
        instead, and ``gui.accessibility`` fails if a control has none.
        """
        from universal_search.gui import accessibility

        for widget, key in (
            (self.entry, "SEARCH.PLACEHOLDER_OR_LABEL"),
            (self.context_combo, "SEARCH.LABEL.CONTEXT"),
            (self.source_combo, "SEARCH.LABEL.SOURCE"),
            (self.type_combo, "SEARCH.LABEL.TYPE"),
            (self.recent_button, "SEARCH.RECENTS"),
            (self.listbox, "RESULTS.LIST_LABEL"),
        ):
            accessibility.declare_name(widget, strings.get(key))

    def _bind_keys(self) -> None:
        self.entry.bind("<Return>", self._on_open)
        self.entry.bind("<Control-Return>", self._on_reveal)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Down>", self._move_down)
        self.entry.bind("<Up>", self._move_up)
        self.entry.bind("<Next>", self._page_down)
        self.entry.bind("<Prior>", self._page_up)
        self.listbox.bind("<Return>", self._open_selected)
        self.listbox.bind("<Control-Return>", self._reveal_selected)
        self.listbox.bind("<Escape>", self._on_escape)
        self.listbox.bind("<Double-Button-1>", self._open_selected)
        self.listbox.bind("<<ListboxSelect>>", self._update_preview)
        self.listbox.bind("<Control-c>", self._copy_selected_paths)
        # Phase 035: batch actions over a multi-selection.
        self.listbox.bind("<Control-o>", self._open_selected)
        self.listbox.bind("<Control-r>", self._reveal_selected)
        self.listbox.bind("<Control-Shift-R>", self._forget_selected)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_indexer()
        self._poll_show_request()

    # -- background indexer hooks ------------------------------------------------

    def _poll_indexer(self) -> None:
        """Refresh the indexer state in the status bar every two seconds."""
        if self.closed:
            return
        try:
            summary = services.indexer_summary(self.service.paths)
        except Exception:
            log.exception("could not read indexer status")
            summary = "indexador: ?"
        self.indexer_var.set(summary)
        self.after(2000, self._poll_indexer)

    # -- global hotkey signaling -------------------------------------------------

    def _poll_show_request(self) -> None:
        """Consume the worker's show-request flag (global hotkey pressed)."""
        if self.closed:
            return
        try:
            if services.consume_show_request(self.service.paths):
                self._present()
            if services.consume_diagnostics_request(self.service.paths):
                self._show_diagnostics()
        except Exception:
            log.exception("could not handle show request")
        self.after(SHOW_POLL_MS, self._poll_show_request)

    def _present(self) -> None:
        """Bring the window forward and put the cursor in the query box."""
        if self.state() == "iconic":
            self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.entry.focus_force()
        self._set_status(strings.get("SEARCH.HOTKEY_SHOWN"))

    def _indexer_action(self, action: str) -> None:
        handlers = {
            "start": services.start_indexer,
            "stop": services.stop_indexer,
            "pause": services.pause_indexer,
            "resume": services.resume_indexer,
        }
        try:
            message = handlers[action](self.service.paths)
        except Exception:
            log.exception("indexer control failed: %s", action)
            self._set_status(strings.get("INDEXER.ERROR"), "error")
            return
        self._set_status(message)
        self._poll_indexer_now()

    def _poll_indexer_now(self) -> None:
        try:
            self.indexer_var.set(services.indexer_summary(self.service.paths))
        except Exception:
            log.exception("could not read indexer status")

    def _toggle_autostart(self) -> None:
        enabled = bool(self.autostart_var.get())
        try:
            message = services.set_autostart(enabled)
        except Exception:
            log.exception("could not update autostart")
            self.autostart_var.set(not enabled)  # revert the checkbox
            self._set_status(strings.get("ERROR.AUTOSTART"), "error")
            return
        try:
            self.service.save_config(
                replace(self.service.config, start_with_windows=enabled)
            )
        except Exception:
            log.exception("could not persist autostart flag")
        self._set_status(message)

    # -- searching ------------------------------------------------------------

    def _on_query_changed(self, *_args) -> None:
        self._cancel_pending_search()
        if not self.query_var.get().strip():
            self._clear_results()
            self._set_status(strings.get("SEARCH.READY"))
            return
        self._search_job = self.after(DEBOUNCE_MS, self._run_scheduled_search)

    def _run_scheduled_search(self) -> None:
        self._search_job = None
        self._execute_search()

    def _execute_search(self) -> None:
        """Start a search *without* blocking the UI thread (spec 017).

        Typing must never freeze the window: the query runs on a worker
        thread and hands the results back through a queue that the main
        loop drains. Every search carries a generation number, so results
        from a superseded keystroke are dropped instead of replacing the
        newer answer.
        """
        query = self.query_var.get()
        self.context_combo.configure(values=self._context_values())  # keep list fresh
        self._refresh_recent_menu()
        self._generation += 1
        generation = self._generation
        self._inflight += 1
        source = self._active_source_filter()
        doc_type = self._active_type_filter()
        self._set_busy(query)

        def work() -> None:
            try:
                results = self.service.search(
                    query, limit=DEFAULT_LIMIT, source=source, doc_type=doc_type
                )
                self._results_queue.put((generation, results, None, None))
            except QueryError as exc:
                self._results_queue.put((generation, [], str(exc), None))
            except Exception as exc:  # pragma: no cover - defensive
                # The typed query never reaches the log: `events.jsonl`
                # redacts query fields and the support bundle declares it
                # carries no query text, so writing it here would make the
                # product contradict its own privacy contract. The length is
                # enough to tell "empty" from "typo" in a bug report.
                log.exception(
                    "search failed (query of %d characters, %s)",
                    len(query),
                    type(exc).__name__,
                )
                self._results_queue.put((generation, [], None, f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, name="search", daemon=True).start()
        self._poll_results()

    def _poll_results(self) -> None:
        """Drain finished searches on the main thread (Tk is not thread-safe)."""
        if self.closed:
            return
        while True:
            try:
                generation, results, query_error, failure = self._results_queue.get_nowait()
            except queue.Empty:
                break
            self._inflight = max(0, self._inflight - 1)
            if generation != self._generation:
                continue  # a newer keystroke already superseded this answer
            self._apply_search(results, query_error, failure)
        self._result_poll = self.after(RESULT_POLL_MS, self._poll_results)

    def pump(self, timeout: float = 2.0) -> bool:
        """Process events until no search is in flight; True when idle.

        The UI never calls this — it exists so tests and scripts can wait
        for the same asynchronous contract the user experiences, instead
        of reaching into private state.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.update()
            idle = (
                self._inflight == 0
                and self._related_inflight == 0
                and self._search_job is None
                and self._results_queue.empty()
                and self._related_queue.empty()
            )
            if idle:
                # One more cycle so a result queued by a worker that just
                # finished is applied before the caller looks.
                self.update()
                if self._inflight == 0 and self._results_queue.empty():
                    return True
            time.sleep(0.005)
        return False

    def _apply_search(
        self,
        results: list[SearchResult],
        query_error: str | None,
        failure: str | None,
    ) -> None:
        if query_error is not None:
            self._render([])
            self._set_status(f"Consulta no válida: {query_error}")
            return
        if failure is not None:
            self._set_status(strings.get("ERROR.SEARCH"), "error")
            return
        self._render(results)

    # -- personal context -------------------------------------------------------

    def _context_values(self) -> list[str]:
        return ["(todos)"] + [
            context.name for context in load_contexts(self.service.config)
        ]

    def _on_context_changed(self, _event=None) -> None:
        label = self.context_var.get()
        name = "" if label == "(todos)" else label
        try:
            self.service.save_config(replace(self.service.config, active_context=name))
        except Exception:
            log.exception("could not persist the active context")
            self._set_status(strings.get("ERROR.SAVE_CONTEXT"), "error")
            return
        if self.query_var.get().strip():
            self._execute_search()
        self._set_status(f"Contexto: {name or 'ninguno'}")

    # -- filters and recent queries ----------------------------------------------

    def _active_source_filter(self) -> str | None:
        value = self.source_var.get()
        return None if value == SOURCE_FILTER_VALUES[0] else value

    def _active_type_filter(self) -> str | None:
        value = self.type_var.get()
        return None if value == TYPE_FILTER_VALUES[0] else value

    def _on_filter_changed(self, _event=None) -> None:
        if self.query_var.get().strip():
            self._execute_search()
        self._set_status(f"Filtro: {self.source_var.get()} · {self.type_var.get()}")

    def _refresh_recent_menu(self) -> None:
        """Show/hide the recents menu according to configuration (optional)."""
        if not self.service.config.recent_queries_enabled:
            self.recent_button.pack_forget()
            return
        self.recent_button.pack(side="right")
        self.recent_menu.delete(0, "end")
        entries = self.service.config.recent_queries
        if not entries:
            self.recent_menu.add_command(
                label=strings.get("SEARCH.RECENTS_EMPTY"), state="disabled"
            )
            return
        for entry in entries:
            self.recent_menu.add_command(
                label=entry, command=lambda query=entry: self._apply_recent(query)
            )

    def _apply_recent(self, query: str) -> None:
        self.query_var.set(query)  # the trace schedules a search…
        self._cancel_pending_search()  # …but we run it immediately instead
        self._execute_search()

    def _cancel_pending_search(self) -> None:
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except Exception:  # job already ran
                pass
            self._search_job = None

    def _render(self, results: list[SearchResult]) -> None:
        self.results = results
        self.listbox.delete(0, "end")
        for result in results:
            self.listbox.insert("end", self._row_text(result))
        if results:
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.listbox.see(0)
            self._set_status(f"{len(results)} resultado(s)")
        elif self.query_var.get().strip():
            # A named, empty result list is a state of its own (spec 017):
            # say what happened and what to try, not just "0".
            self._set_status(
                f"Sin resultados para «{self.query_var.get().strip()}»"
            )
        else:
            self._set_status(strings.get("SEARCH.READY"))
        self._update_preview()

    @staticmethod
    def _row_text(result: SearchResult) -> str:
        return rows.format_result_row(
            result.name, result.path, result.snippet, result.source
        )

    def _set_busy(self, query: str) -> None:
        """Loading state: the previous results stay visible while searching.

        Nothing is cleared and nothing is disabled: a search box that
        blanks on every keystroke makes it impossible to compare two
        queries. ``_apply_search`` replaces the status when results land.
        """
        self._set_status(f"Buscando «{query.strip()}»…")

    def _clear_results(self) -> None:
        # Bumping the generation makes any search still in flight stale,
        # so late results cannot repopulate a box the user just cleared.
        self._generation += 1
        self.results = []
        self.listbox.delete(0, "end")
        self.preview.configure(text="")

    def _set_status(self, text: str, severity: str = "info") -> None:
        """Show a status line, coloured by how much it matters.

        Phase 039: an error used to be rendered in exactly the same muted grey
        as "Listo — escribe para buscar", so a failure a user needed to read was
        visually identical to a message they could ignore. ``danger`` and
        ``busy`` were in the palette for that and nothing ever used them.
        """
        self.status_var.set(text)
        colour = {
            "error": self.theme.danger,
            "warning": self.theme.busy,
        }.get(severity, self.theme.muted)
        try:
            self.status_label.configure(foreground=colour)
        except tk.TclError:  # pragma: no cover - window already destroyed
            pass

    # -- diagnostics and control center (phases 015/023) ----------------------

    def _show_control_center(self) -> None:
        """Open the separate operational window without crowding search."""
        existing = self.control_center_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except (tk.TclError, RuntimeError, AttributeError):
                pass
        from universal_search.gui.control_center import (
            ControlCenterService,
            ControlCenterWindow,
        )

        self.control_center_window = ControlCenterWindow(
            self,
            service=ControlCenterService(service=self.service),
        )

    def _show_diagnostics(self) -> None:
        """Read-only health report in a window of its own."""
        try:
            report = self.service.diagnostics_report()
        except Exception:
            log.exception("could not build the diagnostics report")
            self._set_status(strings.get("ERROR.DIAGNOSTICS"), "error")
            return
        window = tk.Toplevel(self)
        window.title("Diagnóstico del índice")
        window.geometry("640x420")
        text = tk.Text(window, wrap="word")
        text.insert("1.0", report)
        text.configure(state="disabled")
        text.pack(side="top", fill="both", expand=True, padx=8, pady=8)

    def _show_related(self, document_id: str | None = None) -> None:
        """Load a small ranked evidence list without blocking Tk."""
        reference = document_id
        if reference is None:
            index = self._selected_index()
            if index is None or not self.results:
                self._set_status(strings.get("ACTION.RELATED_NEEDS_SELECTION"))
                return
            result = self.results[index]
            reference = result.document_id or str(result.path)
        self._related_generation += 1
        generation = self._related_generation
        self._related_inflight += 1
        self._set_status(strings.get("RELATED.LOADING"))

        def work() -> None:
            try:
                related = self.service.related(reference)
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("could not load related documents")
                self._related_queue.put((generation, [], str(exc)))
            else:
                self._related_queue.put((generation, related, None))

        threading.Thread(target=work, name="related", daemon=True).start()
        self._poll_related()

    def _poll_related(self) -> None:
        """Drain related work on the Tk thread, dropping stale generations."""
        if self.closed:
            return
        while True:
            try:
                generation, related, failure = self._related_queue.get_nowait()
            except queue.Empty:
                break
            self._related_inflight = max(0, self._related_inflight - 1)
            if generation != self._related_generation:
                continue
            if failure is not None:
                self._set_status(strings.get("ERROR.RELATED"), "error")
                continue
            self._render_related(related)
        self._related_poll = self.after(RESULT_POLL_MS, self._poll_related)

    def _render_related(self, related) -> None:
        """Create the evidence list after the worker has finished."""
        if self.related_window is not None:
            try:
                if self.related_window.winfo_exists():
                    self.related_window.destroy()
            except tk.TclError:
                pass
        window = tk.Toplevel(self)
        self.related_window = window
        window.title("Documentos relacionados")
        window.geometry("760x360")
        ttk.Label(
            window,
            text=strings.get("RELATED.NOTE"),
            padding=(10, 8),
        ).pack(anchor="w")
        frame = ttk.Frame(window, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)
        if not related:
            ttk.Label(frame, text=strings.get("RELATED.EMPTY")).pack(anchor="nw")
            return
        listbox = tk.Listbox(
            frame,
            selectmode="browse",
            exportselection=False,
            activestyle="none",
            relief="flat",
            highlightthickness=1,
            highlightcolor=self.theme.accent,
            highlightbackground=self.theme.background,
            background=self.theme.background,
            foreground=self.theme.foreground,
            selectbackground=self.theme.selection_background,
            selectforeground=self.theme.selection_foreground,
        )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for item in related:
            evidence = []
            for detail in getattr(item, "evidence", ()):
                values = ", ".join(getattr(detail, "values", ()))
                evidence.append(
                    f"{getattr(detail, 'kind', 'signal')}"
                    + (f": {values}" if values else "")
                )
            reason = "; ".join(evidence[:3]) or "relación local"
            name = getattr(item, "name", "") or item.document_id
            listbox.insert("end", f"{item.score:.3f}  {name}  —  {reason}")
        self._set_status(f"{len(related)} documento(s) relacionado(s)")

    def _rebuild_index(self) -> None:
        """Compatibility route: maintenance belongs to the control center."""
        # Kept as a private compatibility hook for older integrations, but it
        # never performs a synchronous database operation on the Tk thread.
        self._show_control_center()
        self._set_status(strings.get("ACTION.REBUILD_HINT"))

    # -- selection and preview -------------------------------------------------

    def _selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        if selection and selection[0] < len(self.results):
            return selection[0]
        return None

    def _selected_paths(self) -> list[Path]:
        """Paths of the current selection, in result order (phase 035).

        Ordered by result position rather than by click order, so a batch acts
        on what the list shows rather than on the order the user happened to
        click in.
        """
        chosen = set(self.listbox.curselection())
        return [
            result.path
            for index, result in enumerate(self.results)
            if index in chosen
        ]

    def _batch(self) -> BatchOperations:
        return BatchOperations(
            database=getattr(self.service, "database", None)
        )

    def _open_selected(self) -> str:
        """Open the selection: one document opens it, several open all of them.

        One code path for both, deliberately. A separate "open the first one"
        branch is where the two behaviours drift apart and a single click ends
        up meaning something different from a single selection.
        """
        paths = self._selected_paths()
        index = self._selected_index()
        if not paths:
            if index is None and self.results:
                index = 0
            if index is None:
                return "break"
            paths = [self.results[index].path]
            index = 0
        report = self._batch().open_all(paths)
        if len(paths) == 1 and report.ok:
            # Recent queries and the local usage signal are recorded when the
            # user commits to a single result, never per batch item.
            self.service.record_query(self.query_var.get())
            try:
                self.service.record_open(
                    self.results[index].document_id, self.query_var.get()
                )
            except (AttributeError, IndexError):
                pass
        # The report is never replaced by a bare "done": how many opened, how
        # many failed and how many were left out are all in this one line.
        self._set_status(report.summary())
        return "break"

    def _reveal_selected(self) -> str:
        paths = self._selected_paths()
        if not paths:
            index = self._selected_index()
            if index is None and self.results:
                index = 0
            if index is None:
                return "break"
            paths = [self.results[index].path]
        self._set_status(self._batch().reveal_all(paths).summary())
        return "break"

    def _copy_selected_paths(self) -> str:
        paths = self._selected_paths()
        if not paths:
            index = self._selected_index()
            if index is None and self.results:
                index = 0
            if index is None:
                return "break"
            paths = [self.results[index].path]
        text = self._batch().paths_text(paths)
        self.clipboard_clear()
        self.clipboard_append(text)
        count = len(text.splitlines()) if text else 0
        self._set_status(f"Ruta copiada: {paths[0]}" if count == 1
                         else f"{count} rutas copiadas")
        return "break"

    def _forget_selected(self) -> str:
        """Forget the selected documents, after an explicit confirmation."""
        paths = self._selected_paths()
        if not paths:
            self._set_status(strings.get("ACTION.FORGET_NEEDS_SELECTION"))
            return "break"
        plural = "documentos" if len(paths) > 1 else "documento"
        if not messagebox.askyesno(
            "Olvidar documentos",
            f"Se borrarán del índice {len(paths)} {plural}.\n\n"
            "Los archivos del disco no se tocan.\n¿Continuar?",
        ):
            self._set_status(strings.get("ACTION.CANCELLED"))
            return "break"
        report = self._batch().forget_all(paths, confirm=True)
        self._set_status(report.summary())
        return "break"

    def _select(self, index: int) -> None:
        if not self.results:
            return
        index = max(0, min(index, len(self.results) - 1))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self._update_preview()

    def _move_down(self, _event=None):
        current = self._selected_index()
        self._select(0 if current is None else current + 1)
        return "break"

    def _move_up(self, _event=None):
        current = self._selected_index()
        last = len(self.results) - 1
        self._select(last if current is None else current - 1)
        return "break"

    def _page_down(self, _event=None):
        current = self._selected_index()
        self._select(10 if current is None else current + 10)
        return "break"

    def _page_up(self, _event=None):
        current = self._selected_index()
        self._select(-10 if current is None else current - 10)
        return "break"

    def _update_preview(self, _event=None) -> None:
        index = self._selected_index()
        if index is None:
            self.preview.configure(text="")
            return
        result = self.results[index]
        kind = TYPE_LABELS.get(result.path.suffix.lower(), result.path.suffix or "?")
        snippet = (result.snippet or "").replace("[", "").replace("]", "")
        cloud = (
            "  ·  ☁ solo en OneDrive (sin descargar)"
            if result.availability == "cloud_only"
            else ""
        )
        self.preview.configure(
            text=f"{result.name}  —  {kind}  —  {result.source}{cloud}\n{result.path}\n{snippet}"
        )

    # -- actions ----------------------------------------------------------------

    def _on_open(self, _event=None):
        index = self._selected_index()
        if index is None and self.results:
            index = 0
        if index is None:
            return "break"
        result = self.results[index]
        try:
            open_path(result.path)
        except Exception:
            log.exception("could not open %s", result.path)
            self._set_status(strings.get("ERROR.OPEN"), "error")
            return "break"
        # Recent queries (optional, local): recorded when the user commits
        # to a result, never on every intermediate keystroke.
        self.service.record_query(self.query_var.get())
        # Local usage signal — recorded only when the user enabled learning.
        self.service.record_open(result.document_id, self.query_var.get())
        return "break"

    def _on_reveal(self, _event=None):
        index = self._selected_index()
        if index is None and self.results:
            index = 0
        if index is None:
            return "break"
        try:
            reveal_in_explorer(self.results[index].path)
        except Exception:
            log.exception("could not reveal %s", self.results[index].path)
            self._set_status(strings.get("ERROR.REVEAL"), "error")
        return "break"

    def _on_escape(self, _event=None):
        if self.query_var.get():
            self.query_var.set("")  # trace clears the list
        else:
            self._on_close()
        return "break"

    def _copy_path(self, _event=None):
        index = self._selected_index()
        if index is None and self.results:
            index = 0
        if index is None:
            return "break"
        path = str(self.results[index].path)
        self.clipboard_clear()
        self.clipboard_append(path)
        self._set_status(f"Ruta copiada: {path}")
        return "break"

    def _add_root(self) -> None:
        chosen = filedialog.askdirectory(title=strings.get("SEARCH.PICK_FOLDER"))
        if not chosen:
            return
        try:
            result = self.service.control_center().add_source(chosen)
        except Exception:
            log.exception("could not add source through control center")
            self._set_status(strings.get("ERROR.ADD_FOLDER"), "error")
            return
        self.service.reload_config()
        self._set_status(result.message)

    def _on_close(self, _event=None) -> None:
        # Closing the window never touches the indexer: it is a separate
        # process coordinated only through files (spec: GUI and indexer are
        # independent).
        try:
            self.service.save_config(
                replace(self.service.config, window_geometry=self.geometry())
            )
        except Exception:
            log.exception("could not persist window geometry")
        self.closed = True
        services.unregister_gui_pid(self.service.paths)
        self.destroy()


def run() -> int:
    """Entry point for the desktop application; never shows a traceback.

    Single instance (spec 016): when a window is already alive, this
    launch only asks that window to present itself and exits, so a second
    shortcut press or a double launch never produces two windows.
    """
    from universal_search.appconfig import AppPaths, setup_logging
    from universal_search.background import process_alive
    from universal_search.hotkey import (
        clear_gui_pid,
        read_gui_pid,
        request_show,
    )

    setup_logging()
    log.info("Universal Search GUI starting")
    # DPI awareness must be set before Tk creates any HWND (phase 027).
    from universal_search.platforms import get_platform

    try:
        get_platform().set_dpi_awareness()
    except Exception:
        log.exception("could not enable DPI awareness")
    paths = AppPaths.discover()
    existing = read_gui_pid(paths)
    if existing is not None and existing != os.getpid():
        if process_alive(existing) and request_show(paths):
            log.info("another window is already running (pid %s)", existing)
            return 0
    try:
        window = SearchWindow()
    except Exception:
        log.exception("could not start the window")
        return 1
    try:
        window.mainloop()
    except Exception:
        log.exception("window failed")
        return 1
    finally:
        # Only clear the file if it is still ours: a newer window may have
        # taken over while this one was closing.
        if read_gui_pid(paths) == os.getpid():
            clear_gui_pid(paths)
    log.info("Universal Search GUI stopped")
    return 0
