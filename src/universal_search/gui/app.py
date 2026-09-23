"""Universal Search desktop window.

Pure view: widgets, key bindings and rendering only. Every operation goes
through :mod:`universal_search.gui.services`, so the core stays independent
of the GUI and the window can be exercised in tests.
"""

import logging
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, ttk

from universal_search.gui.services import (
    SearchService,
    open_path,
    reveal_in_explorer,
)
from universal_search.index.search import SearchResult

log = logging.getLogger("universal_search.gui")

DEBOUNCE_MS = 150
DEFAULT_LIMIT = 50

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

        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(
            label="Añadir carpeta a indexar…", command=self._add_root
        )
        file_menu.add_separator()
        file_menu.add_command(label="Salir", command=self._on_close)
        menu.add_cascade(label="Archivo", menu=file_menu)
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
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- searching ------------------------------------------------------------

    def _on_query_changed(self, *_args) -> None:
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except Exception:  # job already ran
                pass
            self._search_job = None
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
        try:
            results = self.service.search(query, limit=DEFAULT_LIMIT)
        except Exception:
            log.exception("search failed for %r", query)
            self._set_status("Error al buscar — consulta el registro de errores")
            return
        self._render(results)

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
        if plain:
            return f"{result.name}    {plain[:100]}"
        return result.name

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
        self.preview.configure(
            text=f"{result.name}  —  {kind}  —  {result.source}\n{result.path}\n{snippet}"
        )

    # -- actions ----------------------------------------------------------------

    def _on_open(self, _event=None):
        index = self._selected_index()
        if index is None and self.results:
            index = 0
        if index is None:
            return "break"
        try:
            open_path(self.results[index].path)
        except Exception:
            log.exception("could not open %s", self.results[index].path)
            self._set_status("No se pudo abrir el archivo — consulta el registro")
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

    def _add_root(self) -> None:
        chosen = filedialog.askdirectory(title="Carpeta a indexar")
        if not chosen:
            return
        roots = self.service.config.roots
        if chosen not in roots:
            self.service.save_config(replace(self.service.config, roots=roots + (chosen,)))
        self._set_status(f"Carpeta añadida: {chosen}")

    def _on_close(self, _event=None) -> None:
        try:
            self.service.save_config(
                replace(self.service.config, window_geometry=self.geometry())
            )
        except Exception:
            log.exception("could not persist window geometry")
        self.closed = True
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
