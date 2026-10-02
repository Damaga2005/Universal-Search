"""UX and accessibility contracts of the window (spec 017).

Split in two: the pure parts (theme, scaling, row formatting) are tested
without a display, and the interactive parts use one real Tk window, the
same way the application actually behaves.
"""

import time
from pathlib import Path
from tkinter import ttk

import pytest

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.gui import rows, theme as theme_module
from universal_search.gui import app as gui_app
from universal_search.gui.app import RESULT_POLL_MS, SearchWindow
from universal_search.gui.services import SearchService
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchResult


# -- theme (pure) --------------------------------------------------------------

def test_theme_is_centralised_and_complete():
    for name, palette in theme_module.THEMES.items():
        assert palette.name == name
        # "surface" was removed in phase 039: declared in both palettes and
        # drawn by no widget. A colour nothing renders is a colour nobody
        # audited, and the accessibility contrast table must cover every field.
        for attribute in (
            "background", "foreground", "muted", "accent",
            "selection_background", "selection_foreground", "busy", "danger",
        ):
            value = getattr(palette, attribute)
            assert value.startswith("#") and len(value) == 7, (name, attribute)
    light, dark = theme_module.LIGHT, theme_module.DARK
    # A dark theme must not reuse the light foreground, or text disappears.
    assert light.foreground != dark.foreground
    assert light.background != dark.background


def test_every_palette_colour_is_drawn_somewhere():
    """A colour with no widget using it is dead weight that looks like a
    design decision. This is what caught ``surface``."""
    gui_root = Path(__file__).resolve().parents[1] / "src" / "universal_search" / "gui"
    used: set[str] = set()
    for path in gui_root.glob("*.py"):
        if path.name in {"theme.py", "accessibility.py", "strings.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for field in theme_module.LIGHT.__dataclass_fields__:
            if field in {"name", "dark"}:
                continue
            if f"theme.{field}" in source or f"self.theme.{field}" in source:
                used.add(field)
    declared = {
        field for field in theme_module.LIGHT.__dataclass_fields__
        if field not in {"name", "dark"}
    }
    assert declared - used == set(), (
        f"colores declarados y nunca dibujados: {sorted(declared - used)}"
    )


def test_theme_resolution_honours_configuration_and_never_returns_none():
    assert theme_module.resolve("light") is theme_module.LIGHT
    assert theme_module.resolve("dark") is theme_module.DARK
    assert theme_module.resolve("system", prefer_dark=True) is theme_module.DARK
    assert theme_module.resolve("system", prefer_dark=False) is theme_module.LIGHT
    # An unknown or missing value must not explode the window.
    assert theme_module.resolve("neon", prefer_dark=False) is theme_module.LIGHT
    assert theme_module.resolve("", prefer_dark=False) is theme_module.LIGHT


def test_scaling_is_clamped_and_never_unreadable():
    assert theme_module.clamp_scale(0.1) == 0.75
    assert theme_module.clamp_scale(9.0) == 2.5
    assert theme_module.clamp_scale("not a number") == 1.0
    assert theme_module.scaled_size(10, 0.1) == 8  # never below 8pt
    fonts = theme_module.fonts(2.0)
    assert fonts["entry"][1] > fonts["body"][1]
    assert theme_module.fonts(0.75)["body"][1] >= 8


def test_derived_palette_does_not_mutate_the_original():
    derived = theme_module.with_accent(theme_module.LIGHT, "#123456")
    assert derived.accent == "#123456"
    assert theme_module.LIGHT.accent != "#123456"


# -- result rows (pure) --------------------------------------------------------

def test_result_cells_name_every_part_of_a_result():
    cells = rows.result_cells(
        "informe_final.pdf",
        r"C:\Users\me\Docs\trabajo\informes\informe_final.pdf",
        "resumen [conclusion] del proyecto",
    )
    assert cells["name"] == "informe_final.pdf"
    assert cells["kind"] == "PDF"
    assert cells["folder"] == "trabajo/informes"
    # The absolute user path is noise in a row.
    assert "C:" not in cells["folder"]
    # Highlight markers never reach the screen.
    assert "[" not in cells["snippet"] and "conclusion" in cells["snippet"]
    # And the five things a result is, each has its own cell.
    assert set(cells) == set(gui_app.RESULT_COLUMNS)


def test_result_cells_handle_missing_snippet_and_source():
    cells = rows.result_cells("notas.txt", r"C:\Docs\notas.txt", None)
    assert cells["snippet"] == ""
    assert cells["folder"] == "Docs"
    assert rows.result_cells(
        "notas.txt", r"C:\Docs\notas.txt", "texto", source="onedrive"
    )["source"] == "onedrive"
    # The default source is not worth a column of noise.
    assert rows.result_cells(
        "notas.txt", r"C:\Docs\notas.txt", "texto", source="local"
    )["source"] == ""


def test_one_table_answers_what_kind_of_file_this_is():
    """Phase 041 found two answers living side by side."""
    assert rows.type_label("informe.pdf") == "PDF"
    assert rows.type_label("memoria.docx") == "Word"
    assert rows.type_label("hoja.xlsx") == "Excel"
    # An unknown type still says something useful rather than nothing.
    assert rows.type_label("thing.weird") == "WEIRD"
    assert rows.type_label("noextension") == "—"


def test_snippet_is_truncated_with_an_ellipsis():
    text = "palabra " * 200
    cleaned = rows.clean_snippet(text, limit=40)
    assert len(cleaned) <= 40
    assert cleaned.endswith("…")


def test_path_hint_is_bounded():
    deep = "/a/b/c/d/e/f/archivo.txt"
    assert rows.path_hint(deep) == "e/f"
    assert rows.path_hint("archivo.txt") == ""
    assert rows.path_hint("/a/archivo.txt", parts=1) == "a"


# -- the live window -----------------------------------------------------------

@pytest.fixture(scope="module")
def window(tmp_path_factory, tk_guard):
    root = tmp_path_factory.mktemp("ux")
    files = root / "files" / "electronica"
    files.mkdir(parents=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    (root / "files" / "lejos").mkdir(parents=True)
    (root / "files" / "lejos" / "zzz.txt").write_text("nada", encoding="utf-8")
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(root / "files")
    service = SearchService(
        paths=AppPaths.discover(home=root / "home"), database_path=database.path
    )
    instance = SearchWindow(service=service)
    instance.results = []
    yield instance
    try:
        instance.destroy()
    except Exception:  # pragma: no cover - teardown best effort
        pass


def test_window_uses_the_central_theme(window):
    assert window.theme.name in {"light", "dark"}
    # Phase 041: the results pane is a ttk widget, so it takes the palette
    # through a named style. A `Listbox` took colours as widget options; a
    # Treeview does not, and a pane that kept the old route would have drawn
    # the light palette in the dark theme.
    style = ttk.Style(window)
    assert style.lookup(gui_app.RESULT_STYLE, "fieldbackground") == (
        window.theme.background
    )
    assert style.lookup(gui_app.RESULT_STYLE, "foreground") == window.theme.foreground
    assert style.lookup(gui_app.RESULT_STYLE, "background") == window.theme.background
    selection = style.map(gui_app.RESULT_STYLE, "background")
    assert ("selected", window.theme.selection_background) in selection
    # No hard-coded colour survives in the widget tree.
    assert str(window.cget("background")) == window.theme.background


def test_window_is_keyboard_reachable(window):
    """Focusability as Tk declares it.

    Whether the *window* actually holds the OS focus depends on the
    desktop session and cannot be asserted from a test; that part of the
    contract is in `docs/development/017-ux-checklist.md`, to be verified
    by a person on a real desktop.
    """
    for widget in (window.entry, window.tree):
        assert str(widget.cget("takefocus")) in ("1", "True", "true")


def test_typing_searches_off_the_ui_thread(window, monkeypatch):
    """The handler must return immediately, even with a slow engine."""
    release = __import__("threading").Event()
    started = __import__("threading").Event()

    def slow(query, limit=50, **kwargs):
        started.set()
        release.wait(5)
        return []

    monkeypatch.setattr(window.service, "search", slow)
    window.query_var.set("lento")
    began = time.perf_counter()
    window._execute_search()
    elapsed = time.perf_counter() - began
    try:
        assert elapsed < 0.2, "the UI thread was blocked by the search"
        assert started.wait(5), "the search never started"
        assert window.pump(timeout=0.2) is False  # still in flight
        assert "Buscando" in window.status_var.get()
    finally:
        release.set()
    assert window.pump(timeout=5)
    assert "Sin resultados" in window.status_var.get()


def test_stale_results_never_replace_a_newer_query(window, monkeypatch):
    first = __import__("threading").Event()
    release = __import__("threading").Event()

    def slow(query, limit=50, **kwargs):
        if query == "viejo":
            first.set()
            release.wait(5)
            return [SearchResult(Path("viejo.md"), "viejo.md", "local", "x", 0.0)]
        return []

    monkeypatch.setattr(window.service, "search", slow)
    window.query_var.set("viejo")
    window._execute_search()
    assert first.wait(5)
    window.query_var.set("nuevo")
    window._execute_search()
    release.set()
    assert window.pump(timeout=5)
    assert [r.name for r in window.results] == []
    assert "nuevo" in window.status_var.get() or "Sin resultados" in (
        window.status_var.get()
    )


def test_no_results_state_explains_itself(window):
    window.query_var.set("noexistenadaquienadie")
    window._execute_search()
    assert window.pump(timeout=5)
    status = window.status_var.get()
    assert "Sin resultados" in status
    assert "noexistenadaquienadie" in status


def test_query_error_is_explained_not_traced(window, monkeypatch):
    from universal_search.query import QueryError

    def explode(query, limit=50, **kwargs):
        raise QueryError("operator 'AND' needs a term after it")

    monkeypatch.setattr(window.service, "search", explode)
    window.query_var.set("algo AND")
    window._execute_search()
    assert window.pump(timeout=5)
    status = window.status_var.get()
    assert "no válida" in status
    assert "Traceback" not in status


def test_clearing_the_box_drops_pending_results(window):
    window.query_var.set("capacitor")
    window._execute_search()
    window.query_var.set("")  # triggers _clear_results
    assert window.pump(timeout=5)
    assert window.tree.get_children() == ()
    assert window.results == []


def test_keyboard_navigation_moves_and_opens(window, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr(
        "universal_search.gui.app.open_path", lambda path: opened.append(Path(path))
    )
    window.query_var.set("capacitor")
    window._execute_search()
    assert window.pump(timeout=5)
    assert len(window.tree.get_children()) == 1

    window._move_down()  # wraps around from the first result
    assert window._selected_index() == 0
    window._on_open()
    assert [p.name for p in opened] == ["capacitor.md"]


def test_result_poll_interval_is_short_enough_to_feel_instant():
    assert 0 < RESULT_POLL_MS <= 100


def test_configuration_carries_theme_and_scale(tmp_path: Path):
    paths = AppPaths(tmp_path / "home")
    config = AppConfig(theme="dark", ui_scale=1.5)
    config.save(paths)
    loaded = AppConfig.load(paths)
    assert loaded.theme == "dark"
    assert loaded.ui_scale == pytest.approx(1.5)
    # Defaults stay neutral for existing installations.
    assert AppConfig().theme == "system"
    assert AppConfig().ui_scale == pytest.approx(1.0)
