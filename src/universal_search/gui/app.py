"""Universal Search desktop window.

Pure view: widgets, key bindings and rendering only. Every operation goes
through :mod:`universal_search.gui.services`, so the core stays independent
of the GUI and the window can be exercised in tests.
"""

import logging
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, ttk

from universal_search.context import load_contexts
from universal_search.gui import services
from universal_search.gui.services import (
    SearchService,
    open_path,
    reveal_in_explorer,
)
from universal_search.index.search import SearchResult

log = logging.getLogger("universal_search.gui")

DEBOUNCE_MS = 150
DEFAULT_LIMIT = 50
SHOW_POLL_MS = 250
SOURCE_FILTER_VALUES = ("(todas)", "local", "onedrive")
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
        self.closed = False

        self.title("Universal Search")
        self.geometry(self.service.config.window_geometry or "940x580")
        self.minsize(680, 400)

        self._build_ui()
        self._bind_keys()
        # Publish the PID: the worker's global hotkey targets this window.
        services.register_gui_pid(self.service.paths)
        self._set_status("Listo — escribe para buscar")
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
        self.entry = ttk.Entry(top, textvariable=self.query_var, font=("Segoe UI", 14))
        self.entry.pack(fill="x")

        context_bar = ttk.Frame(top, padding=(0, 6, 0, 0))
        context_bar.pack(fill="x")
        ttk.Label(context_bar, text="Contexto:").pack(side="left")
        self.context_var = tk.StringVar()
        self.context_combo = ttk.Combobox(
            context_bar,
            textvariable=self.context_var,
            state="readonly",
            width=28,
            values=self._context_values(),
        )
        self.context_combo.pack(side="left", padx=(6, 0))
        self.context_combo.bind("<<ComboboxSelected>>", self._on_context_changed)
        self.context_var.set(self.service.config.active_context or "(todos)")

        ttk.Label(context_bar, text="Fuente:").pack(side="left", padx=(12, 0))
        self.source_var = tk.StringVar(value=SOURCE_FILTER_VALUES[0])
        self.source_combo = ttk.Combobox(
            context_bar,
            textvariable=self.source_var,
            state="readonly",
            width=9,
            values=SOURCE_FILTER_VALUES,
        )
        self.source_combo.pack(side="left", padx=(6, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        ttk.Label(context_bar, text="Tipo:").pack(side="left", padx=(12, 0))
        self.type_var = tk.StringVar(value=TYPE_FILTER_VALUES[0])
        self.type_combo = ttk.Combobox(
            context_bar,
            textvariable=self.type_var,
            state="readonly",
            width=8,
            values=TYPE_FILTER_VALUES,
        )
        self.type_combo.pack(side="left", padx=(6, 0))
        self.type_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        self.recent_button = ttk.Menubutton(context_bar, text="Recientes ▾")
        self.recent_menu = tk.Menu(self.recent_button, tearoff=0)
        self.recent_button["menu"] = self.recent_menu
        self.recent_button.pack(side="right")
        self._refresh_recent_menu()

        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(
            label="Añadir carpeta a indexar…", command=self._add_root
        )
        file_menu.add_command(
            label="Copiar ruta del resultado (Ctrl+C)", command=self._copy_path
        )
        file_menu.add_separator()
        file_menu.add_command(label="Salir", command=self._on_close)
        menu.add_cascade(label="Archivo", menu=file_menu)

        self.indexer_menu = tk.Menu(menu, tearoff=0)
        self.indexer_menu.add_command(
            label="Iniciar indexador", command=lambda: self._indexer_action("start")
        )
        self.indexer_menu.add_command(
            label="Detener indexador", command=lambda: self._indexer_action("stop")
        )
        self.indexer_menu.add_command(
            label="Pausar indexación", command=lambda: self._indexer_action("pause")
        )
        self.indexer_menu.add_command(
            label="Reanudar indexación", command=lambda: self._indexer_action("resume")
        )
        self.indexer_menu.add_separator()
        self.autostart_var = tk.BooleanVar(
            value=self.service.config.start_with_windows
        )
        self.indexer_menu.add_checkbutton(
            label="Iniciar con Windows",
            variable=self.autostart_var,
            command=self._toggle_autostart,
        )
        menu.add_cascade(label="Indexador", menu=self.indexer_menu)
        self.config(menu=menu)

        middle = ttk.Frame(self, padding=(12, 0, 12, 0))
        middle.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            middle,
            font=("Segoe UI", 11),
            activestyle="none",
            exportselection=False,
            relief="flat",
            highlightthickness=1,
            highlightcolor="#0078d4",
        )
        scrollbar = ttk.Scrollbar(middle, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        statusbar = ttk.Frame(self, padding=(12, 4))
        statusbar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar()
        ttk.Label(statusbar, textvariable=self.status_var, foreground="#666").pack(
            side="left"
        )
        self.indexer_var = tk.StringVar(value="indexador: …")
        ttk.Label(
            statusbar, textvariable=self.indexer_var, foreground="#666"
        ).pack(side="right")

        self.preview = ttk.Label(
            self, text="", padding=(12, 8), justify="left",
            font=("Segoe UI", 10), foreground="#444",
        )
        self.preview.pack(fill="x", side="bottom")

    def _bind_keys(self) -> None:
        self.entry.bind("<Return>", self._on_open)
        self.entry.bind("<Control-Return>", self._on_reveal)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Down>", self._move_down)
        self.entry.bind("<Up>", self._move_up)
        self.entry.bind("<Next>", self._page_down)
        self.entry.bind("<Prior>", self._page_up)
        self.listbox.bind("<Return>", self._on_open)
        self.listbox.bind("<Control-Return>", self._on_reveal)
        self.listbox.bind("<Escape>", self._on_escape)
        self.listbox.bind("<Double-Button-1>", self._on_open)
        self.listbox.bind("<<ListboxSelect>>", self._update_preview)
        self.listbox.bind("<Control-c>", self._copy_path)
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
        self._set_status("Atajo global — escribe para buscar")

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
            self._set_status("Error en el indexador — consulta el registro")
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
            self._set_status("No se pudo configurar el inicio — consulta el registro")
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
            self._set_status("Listo — escribe para buscar")
            return
        self._search_job = self.after(DEBOUNCE_MS, self._run_scheduled_search)

    def _run_scheduled_search(self) -> None:
        self._search_job = None
        self._execute_search()

    def _execute_search(self) -> None:
        query = self.query_var.get()
        self.context_combo.configure(values=self._context_values())  # keep list fresh
        self._refresh_recent_menu()
        try:
            results = self.service.search(
                query,
                limit=DEFAULT_LIMIT,
                source=self._active_source_filter(),
                doc_type=self._active_type_filter(),
            )
        except Exception:
            log.exception("search failed for %r", query)
            self._set_status("Error al buscar — consulta el registro de errores")
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
            self._set_status("No se pudo guardar el contexto — consulta el registro")
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
                label="(sin búsquedas recientes)", state="disabled"
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
        self._update_preview()

    @staticmethod
    def _row_text(result: SearchResult) -> str:
        plain = " ".join(
            (result.snippet or "").replace("[", "").replace("]", "").split()
        )
        source = f"[{result.source}] "
        if plain:
            return f"{source}{result.name}    {plain[:100]}"
        return f"{source}{result.name}"

    def _clear_results(self) -> None:
        self.results = []
        self.listbox.delete(0, "end")
        self.preview.configure(text="")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # -- selection and preview -------------------------------------------------

    def _selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        if selection and selection[0] < len(self.results):
            return selection[0]
        return None

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
            self._set_status("No se pudo abrir el archivo — consulta el registro")
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
            self._set_status("No se pudo mostrar en el explorador")
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
        chosen = filedialog.askdirectory(title="Carpeta a indexar")
        if not chosen:
            return
        roots = self.service.config.roots
        if chosen not in roots:
            self.service.save_config(replace(self.service.config, roots=roots + (chosen,)))
        self._set_status(f"Carpeta añadida: {chosen}")

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
    """Entry point for the desktop application; never shows a traceback."""
    from universal_search.appconfig import setup_logging

    setup_logging()
    log.info("Universal Search GUI starting")
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
    log.info("Universal Search GUI stopped")
    return 0
