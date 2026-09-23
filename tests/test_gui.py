"""Window-level tests: a real Tk window driven through its bindings."""

import time
from dataclasses import replace
from pathlib import Path

import pytest

from universal_search.appconfig import AppPaths
from universal_search.gui.services import SearchService
from universal_search.index.indexer import Indexer
from universal_search.providers.local import discover_local

tk = pytest.importorskip("tkinter", reason="tkinter not available")


def flush_debounce(window, delay: float = 0.25) -> None:
    """Wait out the search debounce and process the scheduled callback."""
    time.sleep(delay)
    window.update()


@pytest.fixture(scope="module")
def shared_window(tmp_path_factory):
    """One real Tk root for the whole module.

    Creating and destroying several ``Tk()`` instances in one process trips a
    known Tkinter bug (intermittent ``invalid command name "tcl_findLibrary"``),
    so the window lives for the module and each test resets its own state.
    """
    from universal_search.gui import app as gui_app

    tmp = tmp_path_factory.mktemp("gui-window")
    files = tmp / "files"
    files.mkdir()
    (files / "capacitor.md").write_text(
        "capacitor discharge notes", encoding="utf-8"
    )
    (files / "transistor.md").write_text(
        "transistor biasing notes", encoding="utf-8"
    )
    service = SearchService(
        database_path=tmp / "index.db", paths=AppPaths(tmp / "home")
    )
    for document in discover_local(files):
        Indexer(service.database).upsert(document)

    try:
        created = gui_app.SearchWindow(service=service)
    except tk.TclError:
        pytest.skip("tkinter cannot open a display in this environment")
    yield created
    try:
        created.destroy()
    except (tk.TclError, RuntimeError):
        pass


@pytest.fixture
def window(shared_window):
    baseline_config = shared_window.service.config
    shared_window.closed = False
    shared_window.query_var.set("")  # cancels pending debounce jobs
    shared_window._clear_results()
    shared_window._set_status("")
    yield shared_window
    shared_window.query_var.set("")
    shared_window._clear_results()
    shared_window.service.save_config(baseline_config)  # undo per-test config edits


def search(window, query: str) -> None:
    window.query_var.set(query)
    flush_debounce(window)


def test_window_renders_results_preview_and_status(window) -> None:
    search(window, "capacitor")

    assert window.listbox.size() == 1
    assert "capacitor.md" in window.listbox.get(0)
    assert "capacitor" in window.preview["text"]  # context line
    assert str(len(window.results)) in window.status_var.get()
    assert "capacitor.md" in window.preview["text"]
    assert window.results[0].path.name == "capacitor.md"


def assert_bound(widget, sequence: str) -> None:
    """Prove the binding is actually registered on the widget."""
    assert widget.bind(sequence), f"{sequence} is not bound"


def test_keyboard_navigation_and_open(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app

    assert_bound(window.entry, "<Down>")
    assert_bound(window.entry, "<Return>")

    opened: list[Path] = []
    monkeypatch.setattr(gui_app, "open_path", lambda path: opened.append(Path(path)))
    search(window, "notes")  # both documents match; tie broken by path

    window._move_down()  # handler invoked by <Down>
    assert window._selected_index() == 1

    selected_name = window.results[1].name
    window._on_open()  # handler invoked by <Return>

    assert [path.name for path in opened] == [selected_name]


def test_enter_without_selection_opens_first_result(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app

    opened: list[Path] = []
    monkeypatch.setattr(gui_app, "open_path", lambda path: opened.append(Path(path)))
    search(window, "capacitor")
    window.listbox.selection_clear(0, "end")

    window._on_open()

    assert [path.name for path in opened] == ["capacitor.md"]


def test_escape_clears_query_then_closes(window, monkeypatch) -> None:
    # The real window survives the test: only its close action is captured.
    destroyed: list[bool] = []
    monkeypatch.setattr(
        window, "destroy", lambda *args, **kwargs: destroyed.append(True)
    )
    assert_bound(window.entry, "<Escape>")
    search(window, "capacitor")
    assert window.query_var.get() == "capacitor"

    window._on_escape()
    assert window.query_var.get() == ""
    assert window.listbox.size() == 0
    assert not window.closed

    window._on_escape()  # second press closes the application
    assert window.closed
    assert destroyed == [True]


def test_control_enter_reveals_in_explorer(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app

    assert_bound(window.entry, "<Control-Return>")
    revealed: list[Path] = []
    monkeypatch.setattr(gui_app, "reveal_in_explorer", lambda path: revealed.append(Path(path)))
    search(window, "capacitor")

    window._on_reveal()  # handler invoked by <Control-Return>

    assert [path.name for path in revealed] == ["capacitor.md"]


def test_search_error_shows_friendly_status_without_traceback(window, monkeypatch) -> None:
    def boom(query, limit=50):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(window.service, "search", boom)

    window.query_var.set("algo")
    window._execute_search()  # bypass the debounce, call the handler directly

    assert "Error" in window.status_var.get()
    assert "Traceback" not in window.status_var.get()


def test_add_root_saves_config(window, monkeypatch, tmp_path: Path) -> None:
    from universal_search.gui import app as gui_app

    chosen = str(tmp_path / "nueva-carpeta")
    monkeypatch.setattr(gui_app.filedialog, "askdirectory", lambda **kwargs: chosen)

    window._add_root()

    assert chosen in window.service.config.roots
    reloaded = window.service.reload_config()
    assert chosen in reloaded.roots


def test_empty_query_shows_ready_state(window) -> None:
    search(window, "capacitor")
    window.query_var.set("")

    assert window.listbox.size() == 0
    assert window.results == []
    assert "Listo" in window.status_var.get()


# -- background indexer hooks (phase 006) -------------------------------------------

def _menu_labels(menu) -> list[str]:
    return [
        menu.entrycget(index, "label")
        for index in range(menu.index("end") + 1)
        if menu.type(index) == "command"
    ]


def test_indexer_menu_and_status_bar(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app

    labels = _menu_labels(window.indexer_menu)
    for expected in (
        "Iniciar indexador",
        "Detener indexador",
        "Pausar indexación",
        "Reanudar indexación",
    ):
        assert expected in labels

    monkeypatch.setattr(
        gui_app.services,
        "indexer_summary",
        lambda paths=None: "indexador: en reposo",
    )
    window._poll_indexer_now()
    assert "en reposo" in window.indexer_var.get()

    monkeypatch.setattr(
        gui_app.services,
        "start_indexer",
        lambda paths=None: "indexador iniciado (pid 123)",
    )
    window._indexer_action("start")

    assert "indexador iniciado" in window.status_var.get()
    assert "en reposo" in window.indexer_var.get()  # refreshed after the action


def test_indexer_action_errors_stay_friendly(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app

    def boom(paths=None):
        raise RuntimeError("control failed")

    monkeypatch.setattr(gui_app.services, "stop_indexer", boom)
    window._indexer_action("stop")

    assert "Error" in window.status_var.get()
    assert "Traceback" not in window.status_var.get()


def test_cloud_only_result_shows_onedrive_marker(window) -> None:
    from universal_search.index.search import SearchResult

    result = SearchResult(
        path=Path(r"C:\Users\me\OneDrive\nube.md"),
        name="nube.md",
        source="onedrive",
        snippet=None,
        rank=-1.0,
        score=0.5,
        availability="cloud_only",
    )
    window._clear_results()
    window.results = [result]
    window.listbox.insert(0, "nube.md  ·  Markdown  ·  onedrive")
    window.listbox.selection_set(0)
    window._update_preview()

    text = window.preview.cget("text")
    assert "onedrive" in text
    assert "☁" in text


def test_context_combobox_selects_and_persists(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app  # noqa: F401  (module under test)

    window.service.save_config(
        replace(
            window.service.config,
            contexts=({"name": "Universidad", "roots": []},),
            active_context="",
        )
    )
    assert window._context_values() == ["(todos)", "Universidad"]
    assert window.context_var.get() == "(todos)"

    searches: list[bool] = []
    monkeypatch.setattr(window, "_execute_search", lambda: searches.append(True))
    window.query_var.set("capacitor")  # trace only schedules; jobs never run here

    window.context_var.set("Universidad")
    window._on_context_changed()
    assert window.service.config.active_context == "Universidad"
    assert searches == [True]
    assert "Universidad" in window.status_var.get()

    window.context_var.set("(todos)")
    window._on_context_changed()
    assert window.service.config.active_context == ""
    assert len(searches) == 2


def test_open_records_usage_signal_only_when_enabled(window, monkeypatch) -> None:
    from universal_search.gui import app as gui_app
    from universal_search.index.search import SearchResult

    result = SearchResult(
        path=Path(r"C:\usage\y.md"),
        name="y.md",
        source="local",
        snippet=None,
        rank=-1.0,
        score=0.5,
        document_id="doc-usage-test",
    )
    window._clear_results()
    window.results = [result]
    window.listbox.insert(0, "y.md")
    window.listbox.selection_set(0)

    opened: list[str] = []
    monkeypatch.setattr(gui_app, "open_path", lambda path: opened.append(str(path)))

    # default: learning disabled -> the file opens, nothing is recorded
    window._on_open()
    assert opened == [str(result.path)]
    assert window.service.engine.usage_rows() == []

    # enabled -> the open is recorded with its query association
    window.service.save_config(
        replace(window.service.config, usage_tracking=True)
    )
    window.query_var.set("y")
    window._on_open()
    rows = window.service.engine.usage_rows()
    assert len(rows) == 1
    assert rows[0]["document_id"] == "doc-usage-test"
    assert rows[0]["query"] == "y"
    window.service.engine.clear_usage()


def test_filters_apply_to_searches(window, monkeypatch) -> None:
    captured: dict = {}

    def spy(query, limit=50, **kwargs):
        captured.clear()
        captured.update(kwargs)
        captured["query"] = query
        return []

    monkeypatch.setattr(window.service, "search", spy)
    window.query_var.set("practica")

    # no filters by default
    window._execute_search()
    assert captured["source"] is None
    assert captured["doc_type"] is None

    # selecting a filter re-runs the search with it
    window.source_var.set("onedrive")
    window.type_var.set("txt")
    window._on_filter_changed()
    assert captured["source"] == "onedrive"
    assert captured["doc_type"] == "txt"
    assert "Filtro" in window.status_var.get()

    # back to unfiltered for the rest of the suite
    window.source_var.set("(todas)")
    window.type_var.set("(todos)")
    window._execute_search()
    assert captured["source"] is None
    assert captured["doc_type"] is None


def test_copy_path_recents_and_show_request(window, monkeypatch) -> None:
    from universal_search import hotkey
    from universal_search.index.search import SearchResult

    # -- copy path: bound on the results list and observable in the status bar
    assert_bound(window.listbox, "<Control-c>")
    result = SearchResult(
        path=Path(r"C:\docs\nota.md"),
        name="nota.md",
        source="local",
        snippet=None,
        rank=-1.0,
        score=0.5,
        document_id="doc-copy-test",
    )
    window._clear_results()
    window.results = [result]
    window.listbox.insert(0, "nota.md")
    window.listbox.selection_set(0)
    window._copy_path()
    assert "Ruta copiada" in window.status_var.get()
    assert window.clipboard_get() == str(result.path)

    # -- recents menu: visible, populated from configuration, applies queries
    window.service.save_config(
        replace(
            window.service.config,
            recent_queries_enabled=True,
            recent_queries=("fourier", "memoria final"),
        )
    )
    window._refresh_recent_menu()
    assert window.recent_button.winfo_manager() == "pack"
    labels = [
        window.recent_menu.entrycget(index, "label")
        for index in range(window.recent_menu.index("end") + 1)
    ]
    assert labels == ["fourier", "memoria final"]

    executed: list[str] = []
    monkeypatch.setattr(
        window, "_execute_search", lambda: executed.append(window.query_var.get())
    )
    window._apply_recent("fourier")
    assert window.query_var.get() == "fourier"
    assert executed == ["fourier"]
    assert window._search_job is None  # runs immediately, no queued duplicate

    # disabled -> the button disappears (recents are optional)
    window.service.save_config(
        replace(window.service.config, recent_queries_enabled=False)
    )
    window._refresh_recent_menu()
    assert window.recent_button.winfo_manager() == ""

    # -- show request: worker touches the flag, the window consumes it
    hotkey.write_gui_pid(window.service.paths)  # our PID: a live window
    assert hotkey.request_show(window.service.paths) is True
    assert window.service.paths.show_request_file.exists()
    window._poll_show_request()
    assert not window.service.paths.show_request_file.exists()
    assert "Atajo global" in window.status_var.get()
    hotkey.clear_gui_pid(window.service.paths)


def test_window_title_shows_the_product_version(window) -> None:
    from universal_search import __version__

    assert window.title() == f"Universal Search {__version__}"


def test_closing_window_does_not_touch_the_indexer(window, monkeypatch) -> None:
    """Closing the GUI must never stop the background worker (spec)."""
    from universal_search.gui import app as gui_app

    stop_calls: list[bool] = []
    monkeypatch.setattr(
        gui_app.services,
        "stop_indexer",
        lambda paths=None: stop_calls.append(True) or "detenido",
    )
    monkeypatch.setattr(window, "destroy", lambda *args, **kwargs: None)

    window._on_close()

    assert stop_calls == []  # the close path never stops the indexer
    assert window.closed
