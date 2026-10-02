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
from tkinter import filedialog, messagebox, simpledialog, ttk

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
from universal_search.organize import (
    GROUP_FIELDS,
    GROUP_FOLDER,
    GROUP_NONE,
    SORT_FIELDS,
    SORT_RELEVANCE,
    SavedSearch,
    group_results,
    pool_size,
    sort_results,
)
from universal_search.query import SOURCE_KINDS, QueryError

log = logging.getLogger("universal_search.gui")

DEBOUNCE_MS = 150
# How often the main loop collects finished searches. Short enough to feel
# instant, long enough that an idle window does no work.
RESULT_POLL_MS = 40
DEFAULT_LIMIT = 50
SHOW_POLL_MS = 250
# Used only when the widget cannot be asked how many rows it is showing.
DEFAULT_PAGE_ROWS = 10
# The five things a search result is. Phase 041: the old results pane put all
# of them in one string, so the reader had to guess which part was the name
# and which was the folder, and a long name simply vanished past the edge.
RESULT_COLUMNS = ("name", "folder", "kind", "source", "snippet")
# Prefix for a group header row. Headers are not results, so their ids start
# with something a result index can never be, and every selection helper skips
# them by asking whether an id is an index rather than by tracking a second
# list that could fall out of step.
GROUP_ID_PREFIX = "g:"
# A named style, never the built-in "Treeview": see
# `_configure_result_style` for the measurement that decided it.
RESULT_STYLE = "SearchResults.Treeview"
# No dot in this one, on purpose: in a ttk style name the dot separates the
# style from its *layout*, so "Search.Entry" asks for a layout named "Search".
# "SearchResults.Treeview" wants exactly that and inherits the Treeview
# layout; the entry has no such shorthand, so it gets the layout copied.
ENTRY_STYLE = "SearchEntry"
SOURCE_FILTER_VALUES = (
    strings.get("FILTER.ALL_SOURCES"),
    # Phase 042: every kind the query language accepts, including "other".
    # `source:other` was a valid query with no way to pick it from the window.
    *SOURCE_KINDS,
)
TYPE_FILTER_VALUES = (
    strings.get("FILTER.ALL_TYPES"),
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "md",
    "txt",
)
# The catalogue holds one "(todos)" and it serves the context filter too: the
# neutral option for "which context" is the same word the type filter uses.
# A second entry with the same value would break the catalogue's uniqueness
# rule, and a bare literal would break the "every visible string is
# catalogued" rule that phase 039 established.
ALL_CONTEXTS_LABEL = strings.get("FILTER.ALL_TYPES")

# Sort and group are values from `universal_search.organize` and labels from
# the catalogue, kept as two parallel lists because a `ttk.Combobox` returns
# what it was given: the value has to travel, and the value has to be readable.
SORT_VALUES = tuple(SORT_FIELDS)
SORT_LABELS = {
    "relevance": strings.get("SORT.RELEVANCE"),
    "name": strings.get("SORT.NAME"),
    "modified": strings.get("SORT.MODIFIED"),
    "size": strings.get("SORT.SIZE"),
}
GROUP_VALUES = tuple(GROUP_FIELDS)
GROUP_LABELS = {
    GROUP_NONE: strings.get("GROUP.NONE"),
    GROUP_FOLDER: strings.get("GROUP.FOLDER"),
    "type": strings.get("GROUP.TYPE"),
    "source": strings.get("GROUP.SOURCE"),
    "date": strings.get("GROUP.DATE"),
}
GROUP_DISPLAY_LABELS = tuple(GROUP_LABELS[value] for value in GROUP_VALUES)
SORT_DISPLAY_LABELS = tuple(SORT_LABELS[value] for value in SORT_VALUES)


def placeholder_visible(query: str, focused: bool) -> bool:
    """Whether the search box should draw its placeholder.

    A rule, not a widget, so it can be tested without the OS focus: phase 039
    already established that whether a window holds the desktop focus is not
    something a test can assert, and a test that tries will pass on a developer
    machine and fail in CI.
    """
    return not query and not focused


def _add_menu_entry(menu: tk.Menu, kind: str, **options) -> int:
    """Add a menu entry with ``add_<kind>`` and return its index.

    ``Menu.add_command`` returns ``None`` on this Tk, so capturing its result
    and handing it to ``entryconfigure`` configures nothing and then raises
    ``bad menu entry index "-state"``. The index of the entry just added is
    ``menu.index("end")``. Measured here rather than assumed, because the
    failure mode looks like a Tk bug and is actually a return value.
    """
    getattr(menu, f"add_{kind}")(**options)
    return menu.index("end")


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
        # How many results the window shows, and how many it asked the engine
        # for. They differ on purpose: a sort other than relevance can only
        # choose from the set it was given, so phase 036 widened the pool and
        # this phase asks for that wider pool.
        self.limit = DEFAULT_LIMIT
        self.requested_limit = DEFAULT_LIMIT
        # What the last render actually had to do. Phase 042's promise of
        # incremental updates is only worth something if it can be counted.
        self.render_stats: dict[str, int] = {"inserted": 0, "updated": 0, "removed": 0}
        self.suggestions: tuple = ()

        # Theme and scaling resolved once, from configuration (spec 017).
        config = self.service.config
        self.theme = theme_module.resolve(
            getattr(config, "theme", "system")
        )
        self.ui_scale = theme_module.clamp_scale(
            getattr(config, "ui_scale", 1.0)
        )
        self.fonts = theme_module.fonts(self.ui_scale)
        self.spacing = theme_module.spacing(self.ui_scale)
        self.configure(background=self.theme.background)

        self.title(strings.get("APP.TITLE", version=__version__))
        self.geometry(config.window_geometry or "940x580")
        self.minsize(680, 400)

        self._build_ui()
        self._declare_accessible_names()
        self._bind_keys()
        # Publish the PID: the worker's global hotkey targets this window.
        services.register_gui_pid(self.service.paths)
        self._set_status(strings.get("SEARCH.READY"))
        self._show_message(
            strings.get("SEARCH.EMPTY_TITLE"), strings.get("SEARCH.EMPTY_HINT")
        )
        self.query_var.trace_add("write", self._on_query_changed)
        self._sync_placeholder()
        self.entry.focus_set()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        self._configure_result_style(style)
        self._wrap_width = 0

        top = ttk.Frame(self, padding=self.spacing.pad)
        top.pack(fill="x")
        self.query_var = tk.StringVar()
        # The entry lives in its own frame so the placeholder can be placed
        # over it. `place` is the only geometry manager that can overlay a
        # sibling without reserving space for it, which is exactly what a
        # placeholder needs.
        self.search_frame = ttk.Frame(top)
        self.search_frame.pack(fill="x")
        self.entry = ttk.Entry(
            self.search_frame,
            textvariable=self.query_var,
            font=self.fonts["entry"],
            takefocus=True,  # explicit: never rely on a style default
            style=ENTRY_STYLE,
        )
        self.entry.pack(fill="x")
        self.placeholder = ttk.Label(
            self.search_frame,
            text=strings.get("SEARCH.PLACEHOLDER_OR_LABEL"),
            font=self.fonts["entry"],
            foreground=self.theme.muted,
        )
        self.placeholder.place(relx=0.01, rely=0.5, anchor="w")
        self.entry.bind("<FocusIn>", self._sync_placeholder, add="+")
        self.entry.bind("<FocusOut>", self._sync_placeholder, add="+")

        context_bar = ttk.Frame(top, padding=(0, self.spacing.gap, 0, 0))
        context_bar.pack(fill="x")
        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.CONTEXT")).pack(side="left")
        self.context_var = tk.StringVar()
        self.context_combo = ttk.Combobox(
            context_bar,
            textvariable=self.context_var,
            state="readonly",
            width=28,
            values=self._context_values(),
            takefocus=True,  # phase 039: never rely on a style default
        )
        self.context_combo.pack(side="left", padx=(self.spacing.gap, 0))
        self.context_combo.bind("<<ComboboxSelected>>", self._on_context_changed)
        self.context_var.set(
            self.service.config.active_context or ALL_CONTEXTS_LABEL
        )

        gap = (self.spacing.pad, 0)
        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.SOURCE")).pack(
            side="left", padx=gap
        )
        self.source_var = tk.StringVar(value=SOURCE_FILTER_VALUES[0])
        self.source_combo = ttk.Combobox(
            context_bar,
            textvariable=self.source_var,
            state="readonly",
            width=9,
            values=SOURCE_FILTER_VALUES,
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.source_combo.pack(side="left", padx=(self.spacing.gap, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.TYPE")).pack(
            side="left", padx=gap
        )
        self.type_var = tk.StringVar(value=TYPE_FILTER_VALUES[0])
        self.type_combo = ttk.Combobox(
            context_bar,
            textvariable=self.type_var,
            state="readonly",
            width=8,
            values=TYPE_FILTER_VALUES,
            takefocus=True,  # phase 039: never rely on a platform default
        )
        self.type_combo.pack(side="left", padx=(self.spacing.gap, 0))
        self.type_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        # Phase 042: sorting and grouping, from the phase 036 vocabulary. They
        # are filters on *presentation*, so they live on the same row as the
        # other filters and re-render what is already on screen -- asking the
        # engine again for the same query would be wasted work.
        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.SORT")).pack(
            side="left", padx=gap
        )
        self.sort_var = tk.StringVar(value=SORT_DISPLAY_LABELS[0])
        self.sort_combo = ttk.Combobox(
            context_bar,
            textvariable=self.sort_var,
            state="readonly",
            width=12,
            values=SORT_DISPLAY_LABELS,
            takefocus=True,
        )
        self.sort_combo.pack(side="left", padx=(self.spacing.gap, 0))
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_view_changed)

        ttk.Label(context_bar, text=strings.get("SEARCH.LABEL.GROUP")).pack(
            side="left", padx=gap
        )
        self.group_var = tk.StringVar(value=GROUP_DISPLAY_LABELS[0])
        self.group_combo = ttk.Combobox(
            context_bar,
            textvariable=self.group_var,
            state="readonly",
            width=12,
            values=GROUP_DISPLAY_LABELS,
            takefocus=True,
        )
        self.group_combo.pack(side="left", padx=(self.spacing.gap, 0))
        self.group_combo.bind("<<ComboboxSelected>>", self._on_view_changed)

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

        # -- phase 042: saved searches, local history, explanations ---------
        search_menu = tk.Menu(menu, tearoff=0)
        search_menu.add_command(
            label=strings.get("MENU.SEARCH.SAVE"),
            command=self._prompt_store_search,
        )
        self.saved_menu = tk.Menu(search_menu, tearoff=0)
        self.saved_menu.add_command(
            label=strings.get("MENU.SEARCH.SAVED_EMPTY"), state="disabled"
        )
        search_menu.add_cascade(
            label=strings.get("MENU.SEARCH.SAVED"), menu=self.saved_menu
        )
        search_menu.add_command(
            label=strings.get("MENU.SEARCH.DELETE_SAVED"),
            command=self._prompt_delete_search,
        )
        search_menu.add_separator()
        self.suggestion_menu_index = _add_menu_entry(
            search_menu, "command",
            label=strings.get("MENU.SEARCH.NO_SUGGESTION"), state="disabled",
        )
        search_menu.add_separator()
        history_menu = tk.Menu(search_menu, tearoff=0)
        history_menu.add_command(
            label=strings.get("MENU.HISTORY.SHOW"), command=self._show_history
        )
        history_menu.add_command(
            label=strings.get("MENU.HISTORY.CLEAR"), command=self._clear_history
        )
        history_menu.add_separator()
        self.history_disable_index = _add_menu_entry(
            history_menu, "command",
            label=strings.get("MENU.HISTORY.DISABLE"),
            command=lambda: self._set_history_enabled(False),
        )
        self.history_enable_index = _add_menu_entry(
            history_menu, "command",
            label=strings.get("MENU.HISTORY.ENABLE"),
            command=lambda: self._set_history_enabled(True),
        )
        search_menu.add_cascade(
            label=strings.get("MENU.HISTORY"), menu=history_menu
        )
        self.history_menu = history_menu
        menu.add_cascade(label=strings.get("MENU.SEARCH"), menu=search_menu)
        self.search_menu = search_menu

        view_menu = tk.Menu(menu, tearoff=0)
        self.explain_var = tk.BooleanVar(value=False)
        view_menu.add_checkbutton(
            label=strings.get("MENU.VIEW.EXPLAIN"),
            variable=self.explain_var,
            command=self._on_explain_toggled,
        )
        menu.add_cascade(label=strings.get("MENU.VIEW"), menu=view_menu)

        self.config(menu=menu)
        self._refresh_saved_menu()
        self._refresh_history_menu()

        middle = ttk.Frame(self, padding=(self.spacing.pad, 0, self.spacing.pad, 0))
        middle.pack(fill="both", expand=True)
        self.results_frame = ttk.Frame(middle)
        self.results_frame.pack(fill="both", expand=True)
        # Phase 041: columns, not one clipped string per row. The Treeview is
        # in the phase 039 accessible-control list, so the pane that carries
        # the whole product is also the pane the accessibility gates measure —
        # a hand-drawn canvas would have looked more flexible and been
        # invisible to every instrument this project has.
        self.tree = ttk.Treeview(
            self.results_frame,
            columns=RESULT_COLUMNS,
            show="headings",
            # Phase 035: a selection of many results, so they can be opened,
            # revealed or copied in one action.
            selectmode="extended",
            takefocus=True,  # reachable with Tab, visible with the focus ring
            style=RESULT_STYLE,
        )
        for column, key, anchor, width, stretch in (
            ("name", "RESULTS.COLUMN.NAME", "w", 320, False),
            ("folder", "RESULTS.COLUMN.FOLDER", "w", 200, True),
            ("kind", "RESULTS.COLUMN.KIND", "center", self.spacing.column_kind, False),
            (
                "source",
                "RESULTS.COLUMN.SOURCE",
                "center",
                self.spacing.column_source,
                False,
            ),
            ("snippet", "RESULTS.COLUMN.SNIPPET", "w", 320, True),
        ):
            self.tree.heading(column, text=strings.get(key))
            self.tree.column(column, anchor=anchor, width=width, stretch=stretch)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(
            self.results_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")

        # The area where there is nothing to list. Phase 041: "no results" was
        # a status line and a blank rectangle, so the largest region of the
        # window said nothing at all in the state a user meets most often
        # after typing something that does not exist.
        self.empty_frame = ttk.Frame(middle, padding=(0, self.spacing.pad, 0, 0))
        self.empty_title = ttk.Label(
            self.empty_frame,
            text="",
            font=self.fonts["detail"],
            foreground=self.theme.foreground,
            justify="left",
            anchor="w",
            wraplength=1,
        )
        self.empty_title.pack(fill="x")
        self.empty_hint = ttk.Label(
            self.empty_frame,
            text="",
            font=self.fonts["body"],
            foreground=self.theme.muted,
            justify="left",
            anchor="w",
            wraplength=1,
        )
        self.empty_hint.pack(fill="x", pady=(self.spacing.tight, 0))

        statusbar = ttk.Frame(self, padding=(self.spacing.pad, self.spacing.tight))
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
        self.indexer_var = tk.StringVar(
            value=strings.get("INDEXER.STATE_LABEL", state="…")
        )
        self.indexer_label = ttk.Label(
            statusbar, textvariable=self.indexer_var, foreground=self.theme.muted
        )
        self.indexer_label.pack(side="right")

        # The detail pane: what the row cannot fit. Three lines with three
        # roles — what it is, where it is, why it matched — so the full path
        # and the whole snippet are reachable instead of clipped. The path
        # gets the monospaced font, which until phase 041 was defined in the
        # theme and used by nothing.
        self.detail_frame = ttk.Frame(
            self, padding=(self.spacing.pad, 0, self.spacing.pad, self.spacing.gap)
        )
        self.detail_caption = ttk.Label(
            self.detail_frame,
            text=strings.get("RESULTS.DETAIL_LABEL"),
            font=self.fonts["body"],
            foreground=self.theme.muted,
        )
        self.detail_caption.pack(anchor="w")
        self.preview = ttk.Label(
            self.detail_frame,
            text="",
            font=self.fonts["detail"],
            foreground=self.theme.foreground,
            anchor="w",
            justify="left",
        )
        self.preview.pack(fill="x")
        self.preview_path = ttk.Label(
            self.detail_frame,
            text="",
            font=self.fonts["mono"],
            foreground=self.theme.muted,
            anchor="w",
            justify="left",
            wraplength=1,
        )
        self.preview_path.pack(fill="x")
        self.preview_snippet = ttk.Label(
            self.detail_frame,
            text="",
            font=self.fonts["body"],
            foreground=self.theme.muted,
            anchor="w",
            justify="left",
            wraplength=1,
        )
        self.preview_snippet.pack(fill="x")
        # Packed last and to the bottom, so it sits under the status bar.
        self.detail_frame.pack(fill="x", side="bottom")
        self._clear_detail()
        # Wrap to the window instead of clipping at the right edge, which is
        # what the single preview label used to do with every long path.
        self.bind("<Configure>", self._on_window_resize, add="+")

    def _configure_result_style(self, style: ttk.Style) -> None:
        """Put the palette into the results pane through a named style.

        Measured on this machine before committing to it: a *named* ttk style
        overrides ``fieldbackground`` under vista, winnative and clam, while
        the built-in ``Treeview`` style honours it under none of them. A pane
        that keeps the palette in a named style therefore reaches the dark
        theme, and one that relies on the defaults does not.
        """
        style.configure(
            RESULT_STYLE,
            background=self.theme.background,
            fieldbackground=self.theme.background,
            foreground=self.theme.foreground,
            rowheight=self.spacing.row_height,
            font=self.fonts["body"],
        )
        style.map(
            RESULT_STYLE,
            background=[("selected", self.theme.selection_background)],
            foreground=[("selected", self.theme.selection_foreground)],
        )
        # The headings name what each column is, and they are the only part of
        # the pane whose text is not a result. Drawing them in the accent is
        # what makes the structure readable, and it is also what keeps
        # `accent` drawn at all now that the listbox highlight is gone — the
        # phase 039 gate said so before this line existed.
        style.configure(
            f"{RESULT_STYLE}.Heading",
            font=self.fonts["body"],
            foreground=self.theme.accent,
        )
        style.map(
            f"{RESULT_STYLE}.Heading",
            foreground=[("active", self.theme.accent)],
        )
        # The search field is the only control a user looks at while typing,
        # so it is the one that says where the keyboard is: the accent border
        # is the focus ring the Listbox used to draw with `highlightcolor`.
        style.layout(ENTRY_STYLE, style.layout("TEntry"))
        style.configure(
            ENTRY_STYLE,
            fieldbackground=self.theme.background,
            foreground=self.theme.foreground,
            padding=self.spacing.tight,
        )
        for option in ("bordercolor", "lightcolor", "darkcolor"):
            style.map(
                ENTRY_STYLE,
                **{option: [("focus", self.theme.accent)]},
            )

    def _on_window_resize(self, event=None) -> None:
        """Re-wrap the message and detail text to the new window width."""
        if event is not None and event.widget is not self:
            return
        width = self.winfo_width()
        if width <= 1 or width == self._wrap_width:
            return
        self._wrap_width = width
        usable = width - 2 * self.spacing.pad
        for widget in (self.empty_title, self.empty_hint,
                       self.preview_path, self.preview_snippet):
            widget.configure(wraplength=usable)

    def _sync_placeholder(self, _event=None) -> None:
        """Show the placeholder only while the box is empty and not focused."""
        if placeholder_visible(self.query_var.get(), self.focus_get() is self.entry):
            self.placeholder.place(relx=0.01, rely=0.5, anchor="w")
        else:
            self.placeholder.place_forget()

    def _show_message(self, title: str, hint: str = "") -> None:
        """Replace the results area with a message about the results area."""
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.results_frame.pack_forget()
        self.empty_title.configure(text=title)
        self.empty_hint.configure(text=hint)
        if not self.empty_frame.winfo_manager():
            self.empty_frame.pack(fill="both", expand=True)
        self._on_window_resize()
        self._clear_detail()

    def _show_results(self) -> None:
        """Put the results pane back after a message replaced it."""
        self.empty_frame.pack_forget()
        if not self.results_frame.winfo_manager():
            self.results_frame.pack(fill="both", expand=True)

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
            (self.sort_combo, "SEARCH.LABEL.SORT"),
            (self.group_combo, "SEARCH.LABEL.GROUP"),
            (self.recent_button, "SEARCH.RECENTS"),
            (self.tree, "RESULTS.LIST_LABEL"),
        ):
            accessibility.declare_name(widget, strings.get(key))

    def _bind_keys(self) -> None:
        self.entry.bind("<Return>", self._on_open)
        self.entry.bind("<Control-Return>", self._on_reveal)
        # Applying a suggestion is a separate, explicit key: never the same one
        # that accepts what was typed.
        self.entry.bind("<Alt-Return>", self._apply_suggestion)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Down>", self._move_down)
        self.entry.bind("<Up>", self._move_up)
        self.entry.bind("<Next>", self._page_down)
        self.entry.bind("<Prior>", self._page_up)
        self.tree.bind("<Return>", self._open_selected)
        self.tree.bind("<Control-Return>", self._reveal_selected)
        self.tree.bind("<Escape>", self._on_escape)
        self.tree.bind("<Double-Button-1>", self._open_selected)
        self.tree.bind("<<TreeviewSelect>>", self._update_preview)
        # Arrow keys move the selection as well as the cursor, so the detail
        # pane follows the keyboard. ttk moves only the cursor item on its
        # own, which would leave the pane showing the previous result.
        self.tree.bind("<Down>", self._move_down)
        self.tree.bind("<Up>", self._move_up)
        self.tree.bind("<Next>", self._page_down)
        self.tree.bind("<Prior>", self._page_up)
        self.tree.bind("<Home>", self._tree_first)
        self.tree.bind("<End>", self._tree_last)
        self.tree.bind("<Control-c>", self._copy_selected_paths)
        # Phase 035: batch actions over a multi-selection.
        self.tree.bind("<Control-o>", self._open_selected)
        self.tree.bind("<Control-r>", self._reveal_selected)
        self.tree.bind("<Control-Shift-R>", self._forget_selected)
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
            summary = strings.get("INDEXER.UNKNOWN")
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
        # Read here, on the main thread, and nowhere else. A Tk variable
        # touched from a worker raises "main thread is not in main loop", and
        # reading it there would have turned every search into a hard failure
        # the moment anybody asked for explanations.
        explain = self.explain_var.get()
        self.requested_limit = pool_size(self.limit, self._sort_value())
        self._set_busy(query)

        def work() -> None:
            try:
                # `search_or_error`, not `search` plus a second read of
                # `last_query_error`. Phase 042: `search` swallows QueryError
                # into a field on the shared service, and every keystroke runs
                # on its own thread against that same instance -- so reading
                # the field afterwards can hand this keystroke somebody else's
                # error. Phase 041 drew the malformed-query state correctly and
                # could never be shown it, which is what this call fixes.
                results, query_error = self.service.search_or_error(
                    query,
                    limit=self.requested_limit,
                    explain=explain,
                    source=source,
                    doc_type=doc_type,
                )
                self._results_queue.put((generation, results, query_error, None))
            except QueryError as exc:  # pragma: no cover - defence in depth
                # `search_or_error` catches this; reaching here would mean the
                # service changed its contract, and dropping it on the floor
                # would show the user a traceback instead of a sentence.
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
            # Phase 041 found the twin of the defect phase 039 fixed here:
            # a rejected query was rendered in the same muted grey as an
            # ordinary status line, so the one error the user can fix by
            # editing what they typed looked like idle chatter.
            message = strings.get("STATUS.QUERY_INVALID", reason=query_error)
            self._clear_results()
            self._show_message(message, strings.get("SEARCH.QUERY_SYNTAX_HINT"))
            self._set_status(message, "error")
            return
        if failure is not None:
            # And these results used to stay on screen. The user had just
            # typed something new, the search failed, and the previous
            # query's answers were still listed as if they were the answer.
            self._clear_results()
            self._show_message(
                strings.get("ERROR.SEARCH"),
                strings.get("RESULTS.ERROR", reason=failure),
            )
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
        self._set_status(
            strings.get(
                "STATUS.CONTEXT",
                name=name or strings.get("STATUS.CONTEXT_NONE"),
            )
        )

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
        self._set_status(
            strings.get(
                "STATUS.FILTER",
                source=self.source_var.get(),
                type=self.type_var.get(),
            )
        )

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
        """Sort, group, and update the pane with as little work as possible.

        Phase 036's rule governs the first two steps: organising the results
        must not change which results you get. So ``sort_results`` and
        ``group_results`` are the core's own functions, called with what the
        engine returned and nothing else.

        Phase 042's addition is the third step. Deleting every row and
        reinserting fifty of them on every keystroke is the old behaviour, and
        it is wasted work: while a user types, most rows do not move. A row is
        identified by its position, so a row whose five values are unchanged is
        left alone and only the ones that differ are rewritten. The counts are
        kept because an unmeasured claim of incrementality is not a claim.
        """
        self.results = results
        self.suggestions = ()
        # Read before the pane is rewritten: re-sorting or re-grouping must
        # leave the user on the row they were reading, which is the whole
        # reason incremental rendering is worth having.
        previous = self._selected_index()
        ordered = sort_results(results, self._sort_value())
        self._render_rows(ordered)
        self._show_results()
        if results:
            indices = self._result_indices()
            self._select(
                previous if previous in indices else (indices[0] if indices else 0)
            )
            self._set_status(strings.get("RESULTS.COUNT", count=len(results)))
        elif self.query_var.get().strip():
            # A named, empty result list is a state of its own (spec 017):
            # say what happened and what to try, not just "0".
            self._refresh_suggestions()
            message = strings.get("RESULTS.NONE", query=self.query_var.get().strip())
            self._show_message(message, self._empty_hint())
            self._set_status(message)
        else:
            # Nothing typed yet. The message explains the product rather than
            # repeating the status line, which already says it is ready.
            self._show_message(
                strings.get("SEARCH.EMPTY_TITLE"), strings.get("SEARCH.EMPTY_HINT")
            )
            self._set_status(strings.get("SEARCH.READY"))
        self._update_preview()

    def _render_rows(self, ordered: list[SearchResult]) -> None:
        """Put ``ordered`` on screen, reusing the rows that did not change."""
        stats = {"inserted": 0, "updated": 0, "removed": 0}
        group = self._group_value()
        cells = [
            rows.result_cells(r.name, r.path, r.snippet, r.source)
            for r in ordered
        ]
        # A result's row id is its position in the sorted list, so the mapping
        # from a `SearchResult` back to its row is built once and looked up --
        # not found with `list.index`, which compares by value and would pair
        # two identical results with the same row.
        position = {id(result): index for index, result in enumerate(ordered)}
        wanted: dict[str, tuple | None] = {}
        headers: dict[str, str] = {}
        order: list[str] = []

        if group == GROUP_NONE:
            for index, row in enumerate(cells):
                wanted[str(index)] = tuple(row[c] for c in RESULT_COLUMNS)
                order.append(str(index))
        else:
            for number, block in enumerate(group_results(ordered, group)):
                header = f"{GROUP_ID_PREFIX}{number}"
                label = GROUP_LABELS.get(block.key, strings.get("GROUP.NONE"))
                headers[header] = strings.get("GROUP.HEADER", label=label)
                wanted[header] = None
                order.append(header)
                for result in block.results:
                    index = position[id(result)]
                    row = cells[index]
                    wanted[str(index)] = tuple(row[c] for c in RESULT_COLUMNS)
                    order.append(str(index))

        for existing in self.tree.get_children(""):
            if existing not in wanted:
                self.tree.delete(existing)
                stats["removed"] += 1

        for iid in order:
            values = wanted[iid]
            if values is None:
                # Group header rows carry an id no result index can be, so every
                # selection helper skips them by asking "is this an index?".
                if not self.tree.exists(iid):
                    self.tree.insert(
                        "", "end", iid=iid, text=headers[iid], tags=("group",)
                    )
                    stats["inserted"] += 1
                continue
            if self.tree.exists(iid):
                if tuple(self.tree.item(iid, "values")) != values:
                    self.tree.item(iid, values=values)
                    stats["updated"] += 1
                continue
            self.tree.insert("", "end", iid=iid, values=values)
            stats["inserted"] += 1

        self.render_stats = stats
        # A column full of "local" is noise, so the source column only exists
        # when something in this answer is not local.
        self.tree.configure(
            displaycolumns=list(RESULT_COLUMNS)
            if any(row["source"] for row in cells)
            else [c for c in RESULT_COLUMNS if c != "source"]
        )
        self.tree.tag_configure("group", foreground=self.theme.accent)

    def _render_stats(self) -> dict[str, int]:
        return dict(self.render_stats)

    def _result_indices(self) -> list[int]:
        """The result indices currently on screen, in display order."""
        return [
            int(iid) for iid in self.tree.get_children("")
            if iid.isdigit() and not iid.startswith(GROUP_ID_PREFIX)
        ]

    def _sort_value(self) -> str:
        label = self.sort_var.get()
        for value, text in SORT_LABELS.items():
            if text == label:
                return value
        return SORT_RELEVANCE

    def _group_value(self) -> str:
        label = self.group_var.get()
        for value, text in GROUP_LABELS.items():
            if text == label:
                return value
        return GROUP_NONE

    # -- suggestions (phase 032, reached from the window in phase 042) ------

    def _refresh_suggestions(self) -> None:
        """Ask for verified corrections, once, and only for an empty answer.

        The suggester runs the proposed query against the same engine to prove
        it finds something, so this costs one search -- and only for a query
        that found nothing at all. A query that worked is never second-guessed.
        """
        query = self.query_var.get().strip()
        self.suggestions = self.service.suggest(query) if query else ()
        self._refresh_suggestion_menu()

    def _suggestion_text(self) -> str:
        """The offer, as text for the message area."""
        if not self.suggestions:
            return ""
        return strings.get(
            "SUGGESTION.TEXT", query=self.suggestions[0].query
        )

    def _empty_hint(self) -> str:
        return self._suggestion_text() or strings.get("RESULTS.NONE_HINT")

    def _refresh_suggestion_menu(self) -> None:
        menu = self.search_menu
        index = self.suggestion_menu_index
        if not self.suggestions:
            menu.entryconfigure(index, {
                "label": strings.get("MENU.SEARCH.NO_SUGGESTION"),
                "state": "disabled",
            })
            return
        menu.entryconfigure(index, {
            "label": strings.get(
                "MENU.SEARCH.APPLY_SUGGESTION", query=self.suggestions[0].query
            ),
            "state": "normal",
        })

    def _apply_suggestion(self) -> str:
        """Adopt a suggestion because the user said so. Never otherwise."""
        if not self.suggestions:
            self._set_status(strings.get("SUGGESTION.NONE"))
            return "break"
        proposed = self.suggestions[0].query
        # Setting the variable is the *whole* mechanism: the trace schedules
        # the search like any other keystroke, so a suggestion is not a second
        # way to run a query, it is the user typing the corrected words.
        self.query_var.set(proposed)
        self._set_status(strings.get("STATUS.SUGGESTION", query=proposed))
        return "break"

    # -- saved searches (phase 036 data, phase 042 surface) -----------------

    def _refresh_saved_menu(self) -> None:
        menu = self.saved_menu
        menu.delete(0, "end")
        entries = self.service.saved_searches()
        if not entries:
            menu.add_command(
                label=strings.get("MENU.SEARCH.SAVED_EMPTY"), state="disabled"
            )
            return
        for entry in entries:
            menu.add_command(
                label=entry.name,
                command=lambda chosen=entry.name: self._apply_saved_search(chosen),
            )

    def _current_saved_search(self, name: str) -> SavedSearch:
        return SavedSearch(
            name=name,
            query=self.query_var.get().strip(),
            sort=self._sort_value(),
            group=self._group_value(),
            source=self._active_source_filter() or "",
            doc_type=self._active_type_filter() or "",
        )

    def _prompt_store_search(self) -> None:
        if not self.query_var.get().strip():
            self._set_status(strings.get("STATUS.SAVED_NEEDS_QUERY"))
            return
        name = simpledialog.askstring(
            strings.get("SAVED.PROMPT"), strings.get("SAVED.PROMPT")
        )
        if name is None:
            return
        self._store_current_search(name)

    def _store_current_search(self, name: str) -> None:
        cleaned = " ".join(str(name or "").split())
        if not cleaned:
            self._set_status(strings.get("STATUS.SAVED_EMPTY_NAME"))
            return
        existed = any(
            entry.name.casefold() == cleaned.casefold()
            for entry in self.service.saved_searches()
        )
        self.service.store_saved_search(self._current_saved_search(cleaned))
        self._refresh_saved_menu()
        self._set_status(
            strings.get("STATUS.SAVED_EXISTS" if existed else "STATUS.SAVED", name=cleaned)
        )

    def _prompt_delete_search(self) -> None:
        entries = self.service.saved_searches()
        if not entries:
            self._set_status(strings.get("STATUS.SAVED_MISSING", name=""))
            return
        name = simpledialog.askstring(
            strings.get("MENU.SEARCH.DELETE_SAVED"),
            " · ".join(entry.name for entry in entries),
        )
        if name is None:
            return
        self._delete_saved_search(name)

    def _delete_saved_search(self, name: str) -> None:
        cleaned = " ".join(str(name or "").split())
        if self.service.delete_saved_search(cleaned):
            self._set_status(strings.get("STATUS.SAVED_DELETED", name=cleaned))
        else:
            self._set_status(strings.get("STATUS.SAVED_MISSING", name=cleaned))
        self._refresh_saved_menu()

    def _apply_saved_search(self, name: str) -> None:
        """Put a saved search back: the query and every view that shaped it."""
        entry = None
        for candidate in self.service.saved_searches():
            if candidate.name.casefold() == str(name).casefold():
                entry = candidate
                break
        if entry is None:
            self._set_status(strings.get("STATUS.SAVED_MISSING", name=name))
            return
        self.source_var.set(
            entry.source or SOURCE_FILTER_VALUES[0]
        )
        self.type_var.set(entry.doc_type or TYPE_FILTER_VALUES[0])
        self.sort_var.set(SORT_LABELS.get(entry.sort, SORT_DISPLAY_LABELS[0]))
        self.group_var.set(GROUP_LABELS.get(entry.group, GROUP_DISPLAY_LABELS[0]))
        # The trace fires the search with every field already in place.
        self.query_var.set(entry.query)

    # -- local history (phase 042) ------------------------------------------

    def _refresh_history_menu(self) -> None:
        enabled = self.service.history_enabled()
        # The options go in a dict: `entryconfigure(index, **options)` on this
        # Tk version folds the keywords into the index and fails with
        # 'bad menu entry index "-state"'.
        self.history_menu.entryconfigure(
            self.history_disable_index,
            {"state": "normal" if enabled else "disabled"},
        )
        self.history_menu.entryconfigure(
            self.history_enable_index,
            {"state": "disabled" if enabled else "normal"},
        )

    def _set_history_enabled(self, enabled: bool) -> None:
        self.service.set_history_enabled(enabled)
        self._refresh_history_menu()
        self._refresh_recent_menu()
        self._set_status(
            strings.get(
                "STATUS.HISTORY_ENABLED" if enabled else "STATUS.HISTORY_DISABLED"
            )
        )

    def _clear_history(self) -> str:
        removed = self.service.clear_history()
        self._refresh_recent_menu()
        self._set_status(
            strings.get("STATUS.HISTORY_CLEARED", count=removed)
            if removed
            else strings.get("STATUS.HISTORY_EMPTY")
        )
        return "break"

    def _history_body(self) -> str:
        """What the history view says, as text.

        A method and not a widget: phase 039 established that asserting on a
        Tk widget from a test is unreliable, and this is a claim about wording
        and numbers, not about pixels.
        """
        retention = self.service.history_retention()
        lines: list[str] = []
        if not retention["enabled"]:
            lines.append(strings.get("HISTORY.DISABLED_NOTE"))
            lines.append("")
        lines.append(
            strings.get(
                "HISTORY.RETENTION",
                max_entries=retention["max_entries"],
                max_chars=retention["max_chars"],
                file=retention["file"],
            )
        )
        lines.append("")
        entries = self.service.history()
        lines.extend(entries if entries else (strings.get("HISTORY.EMPTY"),))
        return "\n".join(lines)

    def _show_history(self) -> None:
        """Show what is stored, and the rules that decide what is kept.

        Phase 042 asks for history to be *inspectable*. Showing the rules next
        to the entries is the point: "local and capped" is a promise until
        somebody can read the numbers.
        """
        window = tk.Toplevel(self)
        window.title(strings.get("HISTORY.TITLE"))
        window.geometry("640x420")
        text = tk.Text(window, wrap="word")
        text.insert("1.0", self._history_body())
        text.configure(state="disabled")
        text.pack(
            side="top", fill="both", expand=True,
            padx=self.spacing.gap, pady=self.spacing.gap,
        )

    def _sort_results_only(self, sort: str | None = None) -> None:
        """Re-order what is already on screen, without asking the engine again.

        Phase 036 widened the pool for a non-relevance sort, so the results to
        choose from are already here. Asking again would repeat the whole
        search for a change of order.
        """
        ordered = sort_results(self.results, sort or self._sort_value())
        self._render_rows(ordered)
        self._update_preview()

    def _on_view_changed(self, _event=None) -> None:
        """A presentation change.

        Sorting and grouping normally reorder what is already on screen, which
        is free. One case is not free: a sort other than relevance can only
        choose from the set the engine gave it (phase 036's rule), and the set
        on screen was fetched with the relevance pool. So that one re-runs the
        search with the widened pool, the same thing the CLI does, rather than
        quietly reordering a truncated set and calling it a sort.
        """
        if not self.results:
            return
        sort = self._sort_value()
        relevance_pool = pool_size(self.limit, SORT_RELEVANCE)
        if sort != SORT_RELEVANCE and self.requested_limit <= relevance_pool:
            self._execute_search()
            return
        self._sort_results_only()
        self._set_status(
            strings.get("SORT.APPLIED", label=SORT_LABELS[sort])
        )

    def _on_explain_toggled(self) -> None:
        """Explanations cost work, so they are asked for, never assumed.

        Phase 042 turns ``explain=True`` on only when the user asks, because
        ``Ranker.contributions`` is real work per result and nobody wants it
        paid for on every keystroke they do not read.
        """
        self._set_status(strings.get("STATUS.EXPLAIN"))
        if self.query_var.get().strip():
            self._execute_search()

    def _set_explain(self, enabled: bool) -> None:
        """Turn explanation on or off, and re-run if there is something to run."""
        if bool(enabled) == self.explain_var.get():
            return
        self.explain_var.set(bool(enabled))
        self._on_explain_toggled()

    def _set_busy(self, query: str) -> None:
        """Loading state: the previous results stay visible while searching.

        Nothing is cleared and nothing is disabled: a search box that
        blanks on every keystroke makes it impossible to compare two
        queries. ``_apply_search`` replaces the status when results land.
        """
        self._set_status(strings.get("RESULTS.SEARCHING", query=query.strip()))

    def _clear_results(self) -> None:
        # Bumping the generation makes any search still in flight stale,
        # so late results cannot repopulate a box the user just cleared.
        self._generation += 1
        self.results = []
        self.suggestions = ()
        self._refresh_suggestion_menu()
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)
        self.render_stats = {"inserted": 0, "updated": 0, "removed": 0}
        self._clear_detail()

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
        window.title(strings.get("DIAGNOSTICS.TITLE"))
        window.geometry("640x420")
        text = tk.Text(window, wrap="word")
        text.insert("1.0", report)
        text.configure(state="disabled")
        text.pack(
            side="top", fill="both", expand=True,
            padx=self.spacing.gap, pady=self.spacing.gap,
        )

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
        window.title(strings.get("RELATED.TITLE"))
        window.geometry("760x360")
        ttk.Label(
            window,
            text=strings.get("RELATED.NOTE"),
            padding=(self.spacing.pad, self.spacing.gap),
        ).pack(anchor="w")
        frame = ttk.Frame(window, padding=(self.spacing.pad, 0, self.spacing.pad, self.spacing.pad))
        frame.pack(fill="both", expand=True)
        if not related:
            ttk.Label(frame, text=strings.get("RELATED.EMPTY")).pack(anchor="nw")
            return
        # Same shape as the main results pane: a related document is also a
        # name, a place and a reason, and the old one-line format put the
        # score, the name and the evidence in the same unreadable run as the
        # search list did.
        tree = ttk.Treeview(
            frame,
            columns=("name", "folder", "score", "reason"),
            show="headings",
            selectmode="browse",
            takefocus=True,
            style=RESULT_STYLE,
        )
        for column, key, anchor, width, stretch in (
            ("name", "RESULTS.COLUMN.NAME", "w", 240, False),
            ("folder", "RESULTS.COLUMN.FOLDER", "w", 160, True),
            ("score", "RELATED.COLUMN.SCORE", "center", 64, False),
            ("reason", "RELATED.COLUMN.REASON", "w", 240, True),
        ):
            tree.heading(column, text=strings.get(key))
            tree.column(column, anchor=anchor, width=width, stretch=stretch)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for index, item in enumerate(related):
            evidence = []
            for detail in getattr(item, "evidence", ()):
                values = ", ".join(getattr(detail, "values", ()))
                evidence.append(
                    f"{getattr(detail, 'kind', 'signal')}"
                    + (f": {values}" if values else "")
                )
            reason = "; ".join(evidence[:3]) or strings.get(
                "STATUS.RELATED_FALLBACK"
            )
            name = getattr(item, "name", "") or item.document_id
            path = getattr(item, "path", "") or ""
            tree.insert(
                "", "end", iid=str(index),
                values=(
                    name,
                    rows.path_hint(path) if path else "",
                    f"{item.score:.3f}",
                    reason,
                ),
            )
        tree.selection_set("0")
        self._set_status(
            strings.get("STATUS.RELATED_COUNT", count=len(related))
        )

    def _rebuild_index(self) -> None:
        """Compatibility route: maintenance belongs to the control center."""
        # Kept as a private compatibility hook for older integrations, but it
        # never performs a synchronous database operation on the Tk thread.
        self._show_control_center()
        self._set_status(strings.get("ACTION.REBUILD_HINT"))

    # -- selection and preview -------------------------------------------------

    def _selected_index(self) -> int | None:
        for item in self.tree.selection():
            if item.isdigit() and int(item) < len(self.results):
                return int(item)
        return None

    def _selected_paths(self) -> list[Path]:
        """Paths of the current selection, in result order (phase 035).

        Ordered by result position rather than by click order, so a batch acts
        on what the list shows rather than on the order the user happened to
        click in.
        """
        chosen = {int(item) for item in self.tree.selection() if item.isdigit()}
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
        self._set_status(
            strings.get("STATUS.PATH_COPIED", path=paths[0])
            if count == 1
            else strings.get("STATUS.PATHS_COPIED", count=count)
        )
        return "break"

    def _forget_selected(self) -> str:
        """Forget the selected documents, after an explicit confirmation."""
        paths = self._selected_paths()
        if not paths:
            self._set_status(strings.get("ACTION.FORGET_NEEDS_SELECTION"))
            return "break"
        question = (
            strings.get("ACTION.FORGET_CONFIRM_ONE")
            if len(paths) == 1
            else strings.get("ACTION.FORGET_CONFIRM_MANY", count=len(paths))
        )
        if not messagebox.askyesno(
            strings.get("ACTION.FORGET_CONFIRM_TITLE"), question
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
        # `Treeview.selection_clear()` with no arguments clears *nothing*: Tcl
        # reads the missing item list as "no items to clear", not "everything".
        # Measured, not assumed -- arrowing with it left every row the user had
        # walked past still selected, and the detail pane followed the oldest
        # one instead of the current row.
        chosen = self.tree.selection()
        if chosen:
            self.tree.selection_remove(*chosen)
        self.tree.selection_set(str(index))
        self.tree.focus(str(index))
        self.tree.see(str(index))
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

    def _tree_first(self, _event=None):
        self._select(0)
        return "break"

    def _tree_last(self, _event=None):
        self._select(len(self.results) - 1)
        return "break"

    def _page_size(self) -> int:
        """How many rows a Page Down should move: the rows on screen.

        This used to be the literal 10, in a window whose height and row count
        were both configurable, so a Page Down either skipped past most of a
        short list or crawled through a tall one.

        ``bbox`` only describes rows that are *visible*: it returns an empty
        string for a row scrolled out of sight and raises for one that is not
        there at all. So the count grows from the first row until the pane says
        "no further", which is the question being asked, and both failure modes
        fall back to the constant instead of raising on the UI thread.
        """
        children = self.tree.get_children()
        if len(children) < 2:
            return DEFAULT_PAGE_ROWS
        try:
            first = self.tree.bbox(children[0])
        except tk.TclError:  # the pane was resized between the two calls
            return DEFAULT_PAGE_ROWS
        if not first:
            return DEFAULT_PAGE_ROWS
        visible = 1
        for child in children[1:]:
            try:
                if not self.tree.bbox(child):
                    break
            except tk.TclError:
                break
            visible += 1
        return max(1, visible - 1)

    def _page_down(self, _event=None):
        current = self._selected_index()
        step = self._page_size()
        self._select(step if current is None else current + step)
        return "break"

    def _page_up(self, _event=None):
        current = self._selected_index()
        step = self._page_size()
        self._select(-step if current is None else current - step)
        return "break"

    def _clear_detail(self) -> None:
        self.preview.configure(text="")
        self.preview_path.configure(text="")
        self.preview_snippet.configure(text="")
        if self.detail_frame.winfo_manager():
            self.detail_frame.pack_forget()

    def _update_preview(self, _event=None) -> None:
        """Fill the detail pane: what it is, where it is, why it matched."""
        index = self._selected_index()
        if index is None:
            self._clear_detail()
            return
        result = self.results[index]
        source = result.source or strings.get("FILTER.SOURCE.LOCAL")
        cloud = (
            "  ·  " + strings.get("PREVIEW.CLOUD_ONLY")
            if result.availability == "cloud_only"
            else ""
        )
        self.preview.configure(
            text=f"{result.name}  ·  {rows.type_label(result.path)}  ·  "
                 f"{source}{cloud}"
        )
        self.preview_path.configure(text=str(result.path))
        self.preview_snippet.configure(text=self._why_text(result))
        if not self.detail_frame.winfo_manager():
            self.detail_frame.pack(fill="x", side="bottom")
        self._on_window_resize()

    def _why_text(self, result: SearchResult) -> str:
        """Why this matched: the snippet, or the ranking's own account of it.

        Phase 042 asked for result explanations. The ranking has been computing
        them since phase 004 behind an ``explain`` flag nobody passed, so this
        reads what is already there rather than inventing a second account. The
        plain snippet is still shown when explanations are off, because that is
        what most people want to read.

        The values are `object`, not `float`: the fuzzy layer stores structured
        match details beside its numbers, and this formats both without
        pretending to know which layer produced which.
        """
        if not result.explain:
            return (result.snippet or "").replace("[", "").replace("]", "").strip()
        lines = [strings.get("PREVIEW.EXPLAIN_TITLE")]
        numeric = [
            (signal, float(value))
            for signal, value in result.explain.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        for signal, value in sorted(numeric, key=lambda pair: (-pair[1], pair[0])):
            lines.append(
                strings.get(
                    "PREVIEW.EXPLAIN_SIGNAL", signal=signal, value=value
                )
            )
        for signal, value in sorted(result.explain.items()):
            if signal in {name for name, _ in numeric}:
                continue
            count = len(value) if hasattr(value, "__len__") else 1
            lines.append(
                strings.get(
                    "PREVIEW.EXPLAIN_DETAIL", signal=signal, count=count
                )
            )
        lines.extend(result.explain_notes)
        snippet = (result.snippet or "").replace("[", "").replace("]", "").strip()
        if snippet:
            lines.append("")
            lines.append(snippet)
        return "\n".join(lines)

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
        self._set_status(strings.get("STATUS.PATH_COPIED", path=path))
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
