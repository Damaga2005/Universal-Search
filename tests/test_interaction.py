"""Phase 042: search as one interactive flow, and the seam that was broken.

The first group needs no display. The second drives a real window, because the
claims that matter in this phase are about what a person does: type, filter,
sort, save, come back, act.

The test that started this phase is
:meth:`test_a_rejected_query_reaches_the_user_through_the_real_service`. Phase
041 made the window draw a malformed query as a failure, and its gate measured
that. The gate measured the *handler*; the running application never called it,
because ``SearchService.search`` swallows ``QueryError`` into a field nobody
read. A test that stubs the service can only ever prove the handler works, which
is why this one refuses to stub anything.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from universal_search.appconfig import MAX_QUERY_CHARS, MAX_RECENT_QUERIES, AppPaths
from universal_search.gui import strings
from universal_search.gui import app as gui_app
from universal_search.gui import services as gui_services
from universal_search.organize import MAX_SORT_POOL

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "universal_search" / "gui" / "app.py"


# -- the service layer (no display) -------------------------------------------

@pytest.fixture
def service(tmp_path):
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    files = tmp_path / "files" / "electronica"
    files.mkdir(parents=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    (files / "notas.md").write_text("apuntes de clase", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tmp_path / "files")
    return gui_services.SearchService(
        paths=AppPaths(tmp_path / "home"), database_path=database.path
    )


def test_a_rejected_query_comes_back_with_its_feedback(service):
    """The result and the reason, in one value.

    ``search`` keeps swallowing ``QueryError`` for the CLI, which is its
    contract. The window needs both halves at once, and reading a shared field
    after the call can pick up another keystroke's error.
    """
    results, error = service.search_or_error("algo AND")
    assert results == []
    assert error and "AND" in error
    assert service.last_query_error == error

    results, error = service.search_or_error("capacitor")
    assert results, "a good query must clear the error"
    assert error is None
    assert service.last_query_error is None


def test_search_still_swallows_so_the_cli_contract_holds(service):
    assert service.search("algo AND") == []
    assert service.last_query_error is not None


def test_the_window_gets_the_fuzzy_layer_the_cli_has(service):
    from universal_search.fuzzy import FuzzySearchEngine

    assert isinstance(service.engine, FuzzySearchEngine)


def test_the_fuzzy_layer_can_be_turned_off_from_configuration(tmp_path):
    from universal_search.appconfig import AppConfig
    from universal_search.fuzzy import FuzzySearchEngine
    from universal_search.semantic import HybridSearchEngine

    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(fuzzy_enabled=False).save(paths)
    built = gui_services.SearchService(
        paths=paths, database_path=tmp_path / "off.db"
    )
    assert isinstance(built.engine, HybridSearchEngine)
    assert not isinstance(built.engine, FuzzySearchEngine)


def test_a_typo_gets_a_verified_suggestion_and_the_query_is_not_touched(service):
    """Suggestions are an offer. They are never applied on their own."""
    suggestions = service.suggest("capacitos")
    assert suggestions, "an indexed word with a typo should be corrected"
    assert "capacitor" in suggestions[0].query
    # The suggestion carries a full query, not a patch, so applying it is a
    # decision somebody makes rather than something the engine does.
    assert suggestions[0].query != "capacitos"


def test_a_correct_query_gets_no_suggestion(service):
    assert service.suggest("capacitor") == ()


def test_suggestions_are_immutable_to_the_caller(service):
    suggestions = service.suggest("capacitos")
    assert isinstance(suggestions, tuple)


def test_history_can_be_inspected_cleared_and_switched_off(service):
    for query in ("capacitor", "notas", "electronica"):
        service.record_query(query)
    assert set(service.history()) == {"capacitor", "notas", "electronica"}

    assert service.history_enabled() is True
    service.set_history_enabled(False)
    assert service.history_enabled() is False
    # Switching recording off is not deleting: a user who pauses for a week
    # must find their history intact.
    assert len(service.history()) == 3
    service.record_query("no se guarda")
    assert "no se guarda" not in service.history()

    service.set_history_enabled(True)
    service.record_query("ahora si")
    assert "ahora si" in service.history()

    assert service.clear_history() == 4
    assert service.history() == ()
    assert service.clear_history() == 0


def test_the_retention_rules_are_stated_rather_than_implied(service):
    service.record_query("capacitor")
    retention = service.history_retention()
    assert retention["max_entries"] == MAX_RECENT_QUERIES
    assert retention["max_chars"] == MAX_QUERY_CHARS
    assert retention["kept"] == 1
    assert retention["transmitted"] is False
    # And they say where they live, so "local" is a fact and not a promise.
    assert retention["file"].endswith("config.json")


def test_the_retention_numbers_are_the_policy_and_not_a_copy(service):
    """If the cap changes, the statement must change with it."""
    source = (ROOT / "src" / "universal_search" / "appconfig.py").read_text(
        encoding="utf-8"
    )
    assert f"MAX_RECENT_QUERIES = {MAX_RECENT_QUERIES}" in source
    assert f"MAX_QUERY_CHARS = {MAX_QUERY_CHARS}" in source
    assert "[:MAX_QUERY_CHARS]" in source


def test_saved_searches_round_trip_through_the_service(service):
    from universal_search.organize import SavedSearch, SORT_NAME

    stored = service.store_saved_search(
        SavedSearch(name="electrónica", query="capacitor", sort=SORT_NAME)
    )
    assert stored
    loaded = service.saved_searches()
    assert [entry.name for entry in loaded] == ["electrónica"]
    assert loaded[0].sort == SORT_NAME

    assert service.delete_saved_search("ELECTRÓNICA") is True
    assert service.saved_searches() == ()
    assert service.delete_saved_search("no existe") is False


def test_a_saved_search_holds_no_user_data_beyond_the_six_fields(service):
    from universal_search.organize import SavedSearch

    entry = SavedSearch(name="n", query="q")
    assert set(entry.as_dict()) == {
        "name", "query", "sort", "group", "source", "doc_type"
    }


# -- the window ---------------------------------------------------------------

@pytest.fixture(scope="module")
def window(tmp_path_factory, tk_guard):
    from universal_search.appconfig import AppPaths as Paths
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    root = tmp_path_factory.mktemp("ux-042")
    files = root / "files" / "electronica"
    files.mkdir(parents=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    # A second document that matches the same term, so "capacitor" has more
    # than one answer: the query language ANDs plain terms and stems, and a
    # fixture of one document cannot tell sorting from filtering.
    (files / "resumen.md").write_text(
        "resumen: el capacitor y el diodo en un circuito de desacoplamiento",
        encoding="utf-8",
    )
    (files / "notas.md").write_text("apuntes de clase", encoding="utf-8")
    (root / "files" / "informes").mkdir(parents=True)
    (root / "files" / "informes" / "memoria.pdf").write_bytes(b"%PDF-1.4\n")
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(root / "files")
    service = gui_services.SearchService(
        paths=Paths(root / "home"), database_path=database.path
    )
    instance = gui_app.SearchWindow(service=service)
    yield instance
    try:
        instance.destroy()
    except Exception:  # pragma: no cover - teardown best effort
        pass


@pytest.fixture
def ready(window):
    """A window with nothing typed, no results, and empty local stores.

    The service is module-scoped, so the history and the saved searches are
    cleared here too: otherwise a test that deletes them decides what the next
    one sees, which is how a green suite hides an order dependency.
    """
    window.service.clear_history()
    window.service.set_history_enabled(True)
    window.service.save_config(replace(window.service.config, saved_searches=()))
    window.query_var.set("")
    window._clear_results()
    window._set_status("")
    window.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    window.group_var.set(gui_app.GROUP_DISPLAY_LABELS[0])
    window.source_var.set(gui_app.SOURCE_FILTER_VALUES[0])
    window.type_var.set(gui_app.TYPE_FILTER_VALUES[0])
    window.limit = gui_app.DEFAULT_LIMIT
    window._set_explain(False)
    window._refresh_saved_menu()
    window._render([])
    window.update()
    return window


def _search(window, query, limit=None):
    window.query_var.set(query)
    if limit is not None:
        window.limit = limit
    window._execute_search()
    assert window.pump(timeout=5)


# A query that matches at least two documents in the fixture corpus: the
# query language ANDs plain terms, so "capacitor notas" matches nothing there.
BROAD_QUERY = "capacitor"


def test_a_rejected_query_reaches_the_user_through_the_real_service(window):
    """The defect phase 041's gate could not see.

    Nothing here is stubbed: the malformed query goes through the real service,
    the real engine and the real query parser, and lands in the window's own
    message surface in the failure colour.
    """
    window.query_var.set("")
    window._clear_results()
    _search(window, "algo AND")

    assert window.empty_frame.winfo_manager() != ""
    assert window.empty_title.cget("text").startswith("Consulta no válida")
    assert str(window.status_label.cget("foreground")) == window.theme.danger
    # And the previous answers are gone, rather than being read as the answer
    # to the query that just failed.
    assert window.results == []
    assert window.tree.get_children() == ()


def test_a_good_query_after_a_rejected_one_clears_the_message(window):
    _search(window, "algo AND")
    assert window.empty_frame.winfo_manager() != ""
    _search(window, "capacitor")
    assert window.results
    assert window.empty_frame.winfo_manager() == ""


def test_sorting_does_not_change_which_results_you_get(ready):
    """Phase 036's rule, checked through the window."""
    _search(ready, BROAD_QUERY, limit=10)
    by_relevance = [result.path for result in ready.results]
    assert len(by_relevance) >= 2

    ready.sort_var.set(gui_app.SORT_LABELS["name"])
    ready._sort_results_only()
    by_name = [result.path for result in ready.results]

    assert set(by_name) == set(by_relevance), (
        "sorting reordered the answer; it must not change the answer"
    )
    assert by_name == sorted(by_name, key=lambda p: (p.name.casefold(), str(p)))


def test_an_unknown_sort_is_refused_rather_than_ignored(ready):
    _search(ready, "capacitor")
    before = [result.path for result in ready.results]
    with pytest.raises(ValueError):
        ready._sort_results_only(sort="por arte de magia")
    assert [result.path for result in ready.results] == before


def test_a_non_relevance_sort_asks_for_a_wider_pool(ready):
    _search(ready, "capacitor", limit=5)
    narrow = len(ready.results)
    ready.sort_var.set(gui_app.SORT_LABELS["modified"])
    ready._execute_search()
    ready._sort_results_only()
    ready.show_limit = 5
    # The search for a sort must ask for more than the display limit, or the
    # sort silently chooses from a truncated set.
    assert ready.requested_limit > 5
    assert ready.requested_limit <= MAX_SORT_POOL
    assert len(ready.results) >= min(narrow, 1)


def test_the_group_selector_orders_without_losing_a_single_result(ready):
    _search(ready, BROAD_QUERY, limit=10)
    flat = [result.path for result in ready.results]
    assert len(flat) >= 2

    ready.group_var.set(gui_app.GROUP_LABELS["folder"])
    ready._render(ready.results)
    ready.update()

    shown = [
        path for path in (
            ready.results[index].path
            for index in ready._result_indices()
        )
    ]
    assert shown == flat, "grouping must not lose or invent a result"

    headers = [
        ready.tree.item(item, "text")
        for item in ready.tree.get_children("")
        if item.startswith("g:")
    ]
    assert headers, "no group header was drawn"
    assert all(header for header in headers)


def test_a_group_row_cannot_be_selected_or_opened(ready):
    _search(ready, BROAD_QUERY, limit=10)
    ready.group_var.set(gui_app.GROUP_LABELS["folder"])
    ready._render(ready.results)
    # A real header row, not a made-up id: an absent id would raise and prove
    # nothing about the selection helpers.
    header = next(
        item for item in ready.tree.get_children("") if item.startswith("g:")
    )
    ready.tree.selection_set(header)
    ready._update_preview()
    assert ready._selected_index() is None
    # Opening with no result selected must not raise or invent a path.
    ready._on_open()


def test_home_and_end_visit_results_and_not_group_headers(ready):
    _search(ready, BROAD_QUERY, limit=10)
    ready.group_var.set(gui_app.GROUP_LABELS["folder"])
    ready._render(ready.results)
    first, last = ready._result_indices()[0], ready._result_indices()[-1]
    ready._tree_last()
    assert ready._selected_index() == last
    ready._tree_first()
    assert ready._selected_index() == first


def test_saving_the_current_search_persists_the_whole_view(ready):
    from universal_search.organize import GROUP_TYPE, SORT_NAME

    _search(ready, "capacitor")
    # The combos carry labels; what gets saved is the *value* behind them.
    ready.sort_var.set(gui_app.SORT_LABELS[SORT_NAME])
    ready.group_var.set(gui_app.GROUP_LABELS[GROUP_TYPE])
    ready.source_var.set(gui_app.SOURCE_FILTER_VALUES[1])

    ready._store_current_search("mi búsqueda")

    stored = ready.service.saved_searches()
    assert [entry.name for entry in stored] == ["mi búsqueda"]
    assert stored[0].query == "capacitor"
    assert stored[0].sort == SORT_NAME
    assert stored[0].group == GROUP_TYPE
    assert stored[0].source == "local"


def test_applying_a_saved_search_restores_every_field(ready):
    from universal_search.organize import GROUP_FOLDER, SORT_NAME

    _search(ready, "capacitor")
    ready.sort_var.set(gui_app.SORT_LABELS[SORT_NAME])
    ready.group_var.set(gui_app.GROUP_LABELS[GROUP_FOLDER])
    ready._store_current_search("guardada")

    # Change everything, then apply it back.
    _search(ready, "notas")
    ready.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    ready.group_var.set(gui_app.GROUP_DISPLAY_LABELS[0])
    ready.source_var.set(gui_app.SOURCE_FILTER_VALUES[0])

    ready._apply_saved_search("guardada")

    assert ready.query_var.get() == "capacitor"
    assert ready.sort_var.get() == gui_app.SORT_LABELS[SORT_NAME]
    assert ready.group_var.get() == gui_app.GROUP_LABELS[GROUP_FOLDER]
    assert ready._sort_value() == SORT_NAME
    assert ready._group_value() == GROUP_FOLDER


def test_a_saved_search_can_be_deleted_from_the_window(ready):
    _search(ready, "capacitor")
    ready._store_current_search("temporal")
    assert ready.service.delete_saved_search("temporal") is True
    ready._refresh_saved_menu()
    labels = [
        ready.saved_menu.entrycget(index, "label")
        for index in range(1, ready.saved_menu.index("end") + 1)
    ]
    assert not any("temporal" in label for label in labels)


def test_the_history_view_states_what_is_stored_and_what_is_kept(ready):
    """Inspection, and the retention rules next to the entries.

    Checked through the text the view is built from. A fake ``Toplevel`` was
    tried first and needed a ``tk`` attribute and a ``Text`` that it did not
    have -- the tell that the thing worth testing is the wording, not a widget.
    """
    for query in ("capacitor", "notas"):
        ready.service.record_query(query)
    body = ready._history_body()
    assert "capacitor" in body and "notas" in body
    # And it says the rules, so "local and capped" is a fact with numbers.
    assert str(MAX_RECENT_QUERIES) in body
    assert str(MAX_QUERY_CHARS) in body
    assert "config.json" in body

    ready._set_history_enabled(False)
    disabled = ready._history_body()
    assert strings.get("HISTORY.DISABLED_NOTE") in disabled
    # The entries are still shown: recording is off, nothing was deleted.
    assert "capacitor" in disabled


def test_the_history_view_says_so_when_there_is_nothing(ready):
    ready.service.clear_history()
    assert strings.get("HISTORY.EMPTY") in ready._history_body()


def test_the_history_menu_can_clear_and_switch_off(ready):
    ready.service.record_query("capacitor")
    ready._set_history_enabled(False)
    assert ready.service.history_enabled() is False
    # Switching recording off must not delete what is already stored.
    assert "capacitor" in ready.service.history()
    ready._set_history_enabled(True)
    assert ready.service.history_enabled() is True
    assert strings.get("STATUS.HISTORY_ENABLED") in ready.status_var.get()

    ready.service.record_query("capacitor")
    ready._clear_history()
    assert ready.service.history() == ()
    assert strings.get("STATUS.HISTORY_CLEARED", count=1) in ready.status_var.get()
    # A second clear has nothing to remove and says so, rather than claiming a
    # count it did not delete.
    ready._clear_history()
    assert strings.get("STATUS.HISTORY_EMPTY") in ready.status_var.get()


def test_explanations_are_only_computed_when_asked_for(ready):
    assert ready.explain_var.get() is False
    _search(ready, "capacitor")
    assert ready.results
    assert ready.results[0].explain is None

    ready._set_explain(True)
    _search(ready, "capacitor")
    assert ready.results[0].explain, "explain=True must reach the engine"
    assert ready.results[0].explain_notes or ready.results[0].explain


def test_the_detail_pane_explains_a_result_when_there_is_an_explanation(ready):
    ready._set_explain(True)
    _search(ready, "capacitor")
    ready._select(0)
    ready.update()
    text = ready.preview_snippet["text"]
    assert text, "an explained result must show why it matched"


def test_a_typo_is_answered_by_the_fuzzy_layer_before_any_suggestion(ready):
    """Phases 031 and 042 overlap, and the order matters.

    The fuzzy layer matches by edit distance, so with it on a typo already
    produces results and there is nothing to suggest. Pinning the order is the
    honest way to record it: pretending the suggester is the first line of
    defence would describe a configuration nobody is running.
    """
    assert ready.service.fuzzy_enabled() is True
    _search(ready, "capacitos")
    assert ready.results, "the fuzzy layer should have answered the typo"
    assert ready._suggestion_text() == ""
    # And nothing was rewritten to get there.
    assert ready.query_var.get() == "capacitos"


def test_a_suggestion_is_offered_and_only_applied_on_request(ready):
    """The contract, with the layer that would answer first switched off."""
    ready.service.set_fuzzy_enabled(False)
    _search(ready, "capacitos")
    assert ready.results == [], "lexical alone should find nothing"

    offered = ready._suggestion_text()
    assert "capacitor" in offered
    # Nothing was changed: the query is still exactly what was typed.
    assert ready.query_var.get() == "capacitos"

    ready._apply_suggestion()
    assert ready.query_var.get() == "capacitor"


def test_no_suggestion_is_offered_for_a_query_that_worked(ready):
    ready.service.set_fuzzy_enabled(False)
    _search(ready, "capacitor")
    assert ready.results
    assert ready._suggestion_text() == ""


def test_the_suggestion_is_only_computed_when_there_are_no_results(ready):
    ready.service.set_fuzzy_enabled(False)
    ready._render([])
    assert ready._suggestion_text() == ""
    _search(ready, "capacitos")
    assert ready._suggestion_text()


def test_typing_a_typo_never_rewrites_the_box(ready):
    """The strongest form of the contract: no path may edit the query."""
    ready.service.set_fuzzy_enabled(False)
    _search(ready, "capacitos")
    assert ready.query_var.get() == "capacitos"
    ready.service.set_fuzzy_enabled(True)
    _search(ready, "capacitor")
    assert ready.query_var.get() == "capacitor"


def test_turning_the_fuzzy_layer_off_and_on_leaves_the_engine_working(ready):
    ready.service.set_fuzzy_enabled(False)
    assert ready.service.fuzzy_enabled() is False
    _search(ready, "capacitor")
    assert ready.results, "the lexical engine must still answer"

    ready.service.set_fuzzy_enabled(True)
    assert ready.service.fuzzy_enabled() is True
    _search(ready, "capacitor")
    assert ready.results
    # And the setting survived the trip to disk.
    assert ready.service.config.fuzzy_enabled is True


def test_every_new_control_has_a_takefocus_and_a_name(ready):
    from universal_search.gui.accessibility import (
        focus_report,
        name_report_for,
    )

    report = focus_report(ready)
    assert report.unreachable == (), report.unreachable
    names = name_report_for(ready)
    assert names.unnamed == (), names.unnamed


def test_the_source_filter_list_includes_every_kind_the_query_language_knows():
    """`source:other` was a valid query with no way to pick it."""
    from universal_search.query import SOURCE_KINDS

    offered = set(gui_app.SOURCE_FILTER_VALUES[1:])
    assert set(SOURCE_KINDS) <= offered


def test_the_new_strings_are_catalogued():
    for key in (
        "SEARCH.LABEL.SORT",
        "SEARCH.LABEL.GROUP",
        "GROUP.NONE",
        "MENU.SEARCH.SAVE",
        "MENU.SEARCH.SAVED",
        "MENU.SEARCH.SAVED_EMPTY",
        "MENU.SEARCH.DELETE_SAVED",
        "MENU.SEARCH.APPLY_SUGGESTION",
        "MENU.HISTORY.SHOW",
        "MENU.HISTORY.CLEAR",
        "MENU.HISTORY.DISABLE",
        "MENU.HISTORY.ENABLE",
        "MENU.VIEW.EXPLAIN",
        "STATUS.SAVED",
        "STATUS.SAVED_DELETED",
        "STATUS.HISTORY_CLEARED",
        "STATUS.HISTORY_DISABLED",
        "STATUS.HISTORY_ENABLED",
        "STATUS.SUGGESTION",
        "STATUS.EXPLAIN",
        "SUGGESTION.TEXT",
        "HISTORY.TITLE",
        "HISTORY.RETENTION",
        "PREVIEW.EXPLAIN_TITLE",
        "SAVED.PROMPT",
    ):
        assert key in strings.CATALOGUE, key
    # Removed on purpose: it duplicated PREVIEW.EXPLAIN_TITLE, and the phase 039
    # gate forbids two keys carrying the same value.
    assert "EXPLAIN.TITLE" not in strings.CATALOGUE


def test_no_new_visible_literal_reaches_a_widget(ready):
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"_set_status", "_show_message"}:
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.JoinedStr) or (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
        ):
            offenders.append(f"line {first.lineno} into {node.func.attr}")
    assert offenders == [], offenders


# -- the gate -----------------------------------------------------------------

def test_the_interaction_gate_runs_and_reports_its_verdicts():
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.interaction_gate"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
        env={**_env(), "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout + result.stderr
    if "Tk no disponible" in output:
        pytest.skip("Tk unavailable on this machine: the gate needs a window")
    assert result.returncode in (0, 2), output
    assert output.count("PASS") >= 8, output
    assert "NO SHIP" not in output, output


def _env():
    import os

    return dict(os.environ)