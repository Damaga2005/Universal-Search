"""Window-level tests: a real Tk window driven through its bindings."""

import time
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
