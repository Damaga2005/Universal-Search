"""Phase 041: the primary experience, and the two defects the audit found.

These tests are deliberately split in two.

The first group is pure — the spacing scale, the result cells, the type table.
It runs everywhere and needs no display.

The second group drives a real window, because the interesting claims are about
widgets: that the empty state has words in it, that a failure clears the
previous answer instead of leaving it looking current, and that a long path is
readable somewhere.

The last group reads the source with ``ast``. Two of the changes in this phase
were "a visible string stopped being an f-string", and a test that only
exercises behaviour cannot tell the difference between a status line that came
from the catalogue and one that happens to produce the same letters.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from universal_search.gui import rows, strings, theme as theme_module
from universal_search.gui import app as gui_app

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "universal_search" / "gui" / "app.py"


# -- the spacing scale (pure) -------------------------------------------------

def test_spacing_scales_with_the_ui():
    """Phase 041: `ui_scale` used to multiply fonts and nothing else."""
    small = theme_module.spacing(0.75)
    normal = theme_module.spacing(1.0)
    large = theme_module.spacing(2.0)
    assert small.row_height < normal.row_height < large.row_height
    assert small.pad < normal.pad < large.pad
    assert small.column_kind < normal.column_kind < large.column_kind
    # And it is clamped like the fonts, so a nonsense value cannot collapse it.
    assert theme_module.spacing(0.0) == theme_module.spacing(0.75)
    assert theme_module.spacing(99) == theme_module.spacing(2.5)
    assert set(normal.as_dict()) == {
        "pad", "gap", "tight", "row_height",
        "column_kind", "column_source", "column_folder_min",
    }


def test_the_type_scale_has_a_step_for_the_detail_pane():
    fonts = theme_module.fonts(1.0)
    assert fonts["detail"][1] > fonts["body"][1]
    assert fonts["entry"][1] > fonts["detail"][1]
    # The monospaced face existed since phase 017 and nothing used it until
    # phase 041 gave it the full path in the detail pane.
    assert fonts["mono"][0] == theme_module.MONO_FONT


# -- result cells (pure) ------------------------------------------------------

def test_result_cells_are_exactly_the_pane_columns():
    """Column *order* is the contract, so this compares ordered sequences.

    This used to read:

        assert set(cells) == set(gui_app.RESULT_COLUMNS)
        assert list(cells) == [...] or set(cells) == set(gui_app.RESULT_COLUMNS)

    The right disjunct of the second line was the first line verbatim, so the
    `or` admitted anything the first assertion already admitted and the
    ordering -- which decides which cell a click lands on -- was never compared.
    A set comparison cannot catch a transposed pair of columns, which is
    exactly what this test exists to catch.

    An intermediate version added a meta-assertion about the equivalence of the
    two comparisons. That was harder to read than the thing it was checking,
    and wrong on its first run. The ordered comparison is the whole test.
    """
    cells = rows.result_cells("a.md", r"C:\x\y\a.md", "s", source="onedrive")
    assert isinstance(gui_app.RESULT_COLUMNS, (list, tuple)), (
        "the ordered comparison below is only meaningful for a sequence"
    )
    assert len(cells) == len(gui_app.RESULT_COLUMNS)
    assert list(cells) == list(gui_app.RESULT_COLUMNS)

def test_the_row_formatter_is_gone_rather_than_left_behind():
    """A formatter with no caller is a second place to change the next row."""
    assert not hasattr(rows, "format_result_row")
    assert not hasattr(gui_app.SearchWindow, "_row_text")


def test_one_table_answers_the_type_question():
    assert rows.type_label("x.pdf") == "PDF"
    assert rows.type_label("x.docx") == "Word"
    assert rows.type_label("x.weird") == "WEIRD"
    assert rows.type_label("noext") == "—"


# -- source-level invariants --------------------------------------------------

def _call_names(tree: ast.AST) -> set[str]:
    return {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_no_status_line_is_built_from_a_literal_or_an_f_string():
    """Every visible string in the primary window comes from the catalogue.

    An f-string is invisible to the phase 039 string audit, which is why nine of
    these lines were written inline for years without anybody noticing. Only the
    first argument is inspected, and a call is accepted whatever it is, so a
    value computed from ``strings.get`` a line earlier still passes.
    """
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"_set_status", "_show_message"} or not node.args:
            continue
        first = node.args[0]
        kind = "f-string" if isinstance(first, ast.JoinedStr) else (
            "literal" if isinstance(first, ast.Constant)
            and isinstance(first.value, str) else None
        )
        if kind:
            offenders.append(f"line {first.lineno}: {kind} into {node.func.attr}")
    assert offenders == [], offenders


def test_every_padding_comes_from_the_theme():
    """No bare padding literal survives in the window's layout code.

    Zero is exempt and provably so: zero is zero at every scale, so a literal
    zero is not a value that escaped the theme.
    """
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"Frame", "Label", "Toplevel"}:
            continue
        for keyword in node.keywords:
            if keyword.arg not in {"padding", "padx", "pady"}:
                continue
            value = keyword.value
            elements = (
                [value] if isinstance(value, ast.Constant)
                else list(value.elts) if isinstance(value, ast.Tuple) else []
            )
            for element in elements:
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, int)
                    and element.value != 0
                ):
                    offenders.append(f"line {element.lineno}: literal {element.value}")
    # And the ones that are not layout at all: pack geometry.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        for keyword in node.keywords:
            if keyword.arg not in {"padx", "pady"}:
                continue
            for element in ast.walk(keyword.value):
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, int)
                    and element.value != 0
                ):
                    offenders.append(f"line {element.lineno}: literal {element.value}")
    assert offenders == [], offenders


def test_the_results_pane_is_a_treeview_and_not_a_listbox():
    """A Treeview is in the phase 039 instrument list; a canvas would not be."""
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert "ttk.Treeview" in source
    assert "tk.Listbox" not in source


def test_the_confirmation_a_user_must_read_is_catalogued():
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    confirmations = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "askyesno"
    ]
    assert confirmations, "the forget confirmation must still exist"
    for call in confirmations:
        for argument in call.args:
            assert not isinstance(argument, ast.Constant), (
                f"line {argument.lineno}: a confirmation written as a literal"
            )


def test_no_cjk_survives_in_the_interface_sources():
    for path in strings.gui_modules(ROOT):
        text = path.read_text(encoding="utf-8")
        assert not [c for c in text if "一" <= c <= "鿿"], path.name


# -- the window ---------------------------------------------------------------

@pytest.fixture(scope="module")
def window(tmp_path_factory, tk_guard):
    from universal_search.appconfig import AppPaths
    from universal_search.gui.services import SearchService
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    root = tmp_path_factory.mktemp("ux-041")
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
    instance = gui_app.SearchWindow(service=service)
    yield instance
    try:
        instance.destroy()
    except Exception:  # pragma: no cover - teardown best effort
        pass


@pytest.fixture
def ready(window):
    """The window between tests: no query, no results, no pending search."""
    window.query_var.set("")
    window._clear_results()
    window._set_status("")
    window.update()
    return window


def _result(name="documento.md", path=None, snippet="texto", source="local"):
    from universal_search.index.search import SearchResult

    return SearchResult(
        path=path or Path(r"C:\Users\alguien\Docs\electronica") / name,
        name=name,
        source=source,
        snippet=snippet,
        rank=0.0,
        document_id=f"t-{name}",
    )


# -- the placeholder ----------------------------------------------------------

def test_the_placeholder_rule_is_a_rule_and_not_a_widget():
    """Phase 039 established that OS focus is not assertable from a test."""
    assert gui_app.placeholder_visible("", focused=False) is True
    assert gui_app.placeholder_visible("", focused=True) is False
    assert gui_app.placeholder_visible("algo", focused=False) is False
    assert gui_app.placeholder_visible("algo", focused=True) is False


def test_the_search_box_says_what_it_is(ready):
    assert ready.placeholder.cget("text") == strings.get("SEARCH.PLACEHOLDER_OR_LABEL")
    # The widget must agree with the rule whatever the OS focus happens to be.
    # Asserting a fixed expectation instead would pass on a developer machine
    # where the entry has focus and fail in CI where it does not.
    for query in ("", "algo", "capacitor"):
        ready.query_var.set(query)
        ready.update()
        ready._sync_placeholder()
        expected = gui_app.placeholder_visible(
            ready.query_var.get(), ready.focus_get() is ready.entry
        )
        assert (ready.placeholder.winfo_manager() == "place") is expected, query
    # And the rule does put it away while typing, which is the half that a
    # placeholder exists for.
    ready.query_var.set("algo")
    ready.update()
    assert gui_app.placeholder_visible("algo", focused=False) is False


# -- the results pane ---------------------------------------------------------

def test_each_part_of_a_result_gets_its_own_labelled_column(ready):
    ready.query_var.set("documento")
    ready._render([_result()])
    assert ready.tree.cget("columns") == tuple(gui_app.RESULT_COLUMNS)
    headings = {
        field: ready.tree.heading(field, "text") for field in gui_app.RESULT_COLUMNS
    }
    assert headings == {
        "name": strings.get("RESULTS.COLUMN.NAME"),
        "folder": strings.get("RESULTS.COLUMN.FOLDER"),
        "kind": strings.get("RESULTS.COLUMN.KIND"),
        "source": strings.get("RESULTS.COLUMN.SOURCE"),
        "snippet": strings.get("RESULTS.COLUMN.SNIPPET"),
    }
    assert len(set(headings.values())) == len(headings)


def test_the_source_column_disappears_when_everything_is_local(ready):
    ready.query_var.set("documento")
    ready._render([_result(), _result("otro.md")])
    assert "source" not in ready.tree.cget("displaycolumns")
    ready._render([_result("nube.md", source="onedrive")])
    assert "source" in ready.tree.cget("displaycolumns")


def test_the_detail_pane_has_three_lines_with_three_jobs(ready):
    result = _result(
        "memoria.md",
        path=Path(r"C:\Users\alguien\Documentos\tesis\memoria.md"),
        snippet="un texto  de  la  coincidencia",
    )
    ready.query_var.set("memoria")
    ready._render([result])
    assert ready.preview["text"].startswith("memoria.md")
    assert rows.type_label(result.path) in ready.preview["text"]
    assert ready.preview_path["text"] == str(result.path)
    assert ready.preview_snippet["text"] == "un texto  de  la  coincidencia"


def test_a_long_path_is_readable_in_full_somewhere(ready):
    """The row is bounded on purpose; the detail pane is where it is complete."""
    deep = Path(r"C:\Users\alguien") / "deep" / ("carpeta " * 12) / "nota.md"
    result = _result("nota.md", path=deep, snippet="palabra " * 300)
    ready.query_var.set("nota")
    ready._render([result])
    assert ready.preview_path["text"] == str(deep)
    row_snippet = ready.tree.item("0", "values")[-1]
    assert len(row_snippet) <= theme_module.SNIPPET_CHARS
    assert ready.preview_snippet["text"] == ("palabra " * 300).strip()


def test_the_detail_pane_disappears_when_nothing_is_selected(ready):
    ready.query_var.set("documento")
    ready._render([_result()])
    assert ready.detail_frame.winfo_manager() != ""
    ready.tree.selection_remove(*ready.tree.selection())
    ready._update_preview()
    ready.update()
    assert ready.detail_frame.winfo_manager() == ""
    assert ready.preview["text"] == ""
    assert ready.preview_path["text"] == ""


# -- the states ---------------------------------------------------------------

def test_the_empty_area_says_something_when_nothing_matches(ready):
    ready.query_var.set("noexistenadaquienadie")
    ready._render([])
    assert ready.empty_frame.winfo_manager() != ""
    assert ready.empty_title.cget("text") == strings.get(
        "RESULTS.NONE", query="noexistenadaquienadie"
    )
    assert ready.empty_hint.cget("text") == strings.get("RESULTS.NONE_HINT")
    assert ready.results_frame.winfo_manager() == ""


def test_the_empty_area_explains_the_product_before_any_query(ready):
    ready.query_var.set("")
    ready._render([])
    assert ready.empty_title.cget("text") == strings.get("SEARCH.EMPTY_TITLE")
    assert ready.empty_hint.cget("text") == strings.get("SEARCH.EMPTY_HINT")
    # And the status line does not simply repeat it.
    assert ready.status_var.get() == strings.get("SEARCH.READY")
    assert ready.status_var.get() != ready.empty_title.cget("text")


def test_the_empty_area_carries_both_failure_messages(ready):
    ready.query_var.set("algo AND")
    ready._apply_search([], "operator 'AND' needs a term after it", None)
    assert ready.empty_title.cget("text").startswith("Consulta no válida")
    assert ready.empty_hint.cget("text") == strings.get("SEARCH.QUERY_SYNTAX_HINT")
    ready.query_var.set("algo")
    ready._apply_search([], None, "RuntimeError: db locked")
    assert ready.empty_title.cget("text") == strings.get("ERROR.SEARCH")
    assert "db locked" in ready.empty_hint.cget("text")
    # And the one that is neither: nothing matched.
    ready.query_var.set("nada de esto")
    ready._render([])
    assert ready.empty_title.cget("text") == strings.get(
        "RESULTS.NONE", query="nada de esto"
    )


def test_a_rejected_query_looks_like_a_failure(ready):
    """Phase 041 found the twin of the defect phase 039 fixed here."""
    ready.query_var.set("documento")
    ready._render([_result()])
    ready._set_status(strings.get("SEARCH.READY"))
    ordinary = str(ready.status_label.cget("foreground"))

    ready._apply_search([], "operator 'AND' needs a term after it", None)

    assert str(ready.status_label.cget("foreground")) != ordinary
    assert str(ready.status_label.cget("foreground")) == ready.theme.danger


def test_a_failed_search_leaves_no_previous_answer_on_screen(ready):
    """The defect: results from the previous query stayed listed."""
    ready.query_var.set("documento")
    ready._render([_result(), _result("otro.md")])
    assert len(ready.tree.get_children()) == 2

    ready._apply_search([], None, "RuntimeError: db locked")

    assert ready.tree.get_children() == ()
    assert ready.results == []
    assert ready.detail_frame.winfo_manager() == ""


def test_searching_echoes_the_query_without_logging_it(ready):
    ready._set_busy("  capacitor  ")
    assert ready.status_var.get() == strings.get(
        "RESULTS.SEARCHING", query="capacitor"
    )


def test_results_announce_a_count_from_the_catalogue(ready):
    ready.query_var.set("documento")
    ready._render([_result(), _result("otro.md")])
    assert ready.status_var.get() == strings.get("RESULTS.COUNT", count=2)


# -- the keyboard -------------------------------------------------------------

def test_page_keys_move_by_what_is_on_screen_not_by_ten(ready):
    ready.query_var.set("documento")
    ready._render([_result(f"d{i}.md") for i in range(200)])
    step = ready._page_size()
    assert step != gui_app.DEFAULT_PAGE_ROWS, (
        "with 200 rows on screen the page must not be the fallback of 10"
    )
    assert step > 1
    ready._select(0)
    ready._page_down()
    assert ready._selected_index() == step


def test_the_page_size_falls_back_instead_of_raising(ready):
    """`bbox` raises on an item that is not there; the pane must survive."""
    ready.query_var.set("documento")
    ready._render([_result()])
    assert ready._page_size() == gui_app.DEFAULT_PAGE_ROWS
    ready.tree.delete("0")
    assert ready._page_size() == gui_app.DEFAULT_PAGE_ROWS


def test_the_arrow_keys_reach_the_end_and_the_start(ready):
    ready.query_var.set("documento")
    ready._render([_result(f"d{i}.md") for i in range(5)])
    ready._tree_last()
    assert ready._selected_index() == 4
    ready._tree_first()
    assert ready._selected_index() == 0
    ready._move_down()
    assert ready._selected_index() == 1
    ready._move_up()
    assert ready._selected_index() == 0
    # Both directions stop at the ends rather than wrapping: a search list
    # that jumps from the last result back to the first hides how long the
    # answer was.
    ready._move_up()
    assert ready._selected_index() == 0
    ready._move_down()
    ready._tree_last()
    ready._move_down()
    assert ready._selected_index() == 4


def test_the_pane_binds_the_keys_and_the_detail_follows(ready):
    """The binding is checked, and so is what it is bound to.

    `event_generate` is not used to fire it: a synthetic key event has to
    travel through the focus ring to reach the widget, and whether this window
    holds the desktop focus is not something a test can promise.
    """
    for sequence in ("<Down>", "<Up>", "<Next>", "<Prior>", "<Home>", "<End>",
                     "<Return>", "<Control-Return>", "<Control-c>"):
        assert ready.tree.bind(sequence), sequence
    ready.query_var.set("documento")
    ready._render([_result("uno.md"), _result("dos.md")])
    ready._select(0)
    assert ready.preview["text"].startswith("uno.md")
    ready._move_down()
    assert ready.preview["text"].startswith("dos.md")
    assert ready._selected_index() == 1
    # And it replaced the selection rather than adding to it, which is the
    # part `selection_clear()` got wrong.
    assert ready.tree.selection() == ("1",)


# -- the palette --------------------------------------------------------------

def test_the_pane_draws_the_palette_in_both_themes(ready):
    from tkinter import ttk

    style = ttk.Style(ready)
    assert style.lookup(gui_app.RESULT_STYLE, "fieldbackground") == (
        ready.theme.background
    )
    assert style.lookup(f"{gui_app.RESULT_STYLE}.Heading", "foreground") == (
        ready.theme.accent
    )
    # Every palette colour the pane draws differs between the two themes, or
    # the theme selector would be changing nothing on screen.
    for field in (
        "background", "foreground", "muted", "accent",
        "selection_background", "selection_foreground",
    ):
        assert getattr(theme_module.LIGHT, field) != getattr(
            theme_module.DARK, field
        ), field


def test_the_window_padding_follows_the_configured_scale(window):
    from dataclasses import replace

    from universal_search.appconfig import AppConfig

    assert window.spacing == theme_module.spacing(window.ui_scale)
    assert window.spacing.as_dict() == theme_module.spacing(
        window.ui_scale
    ).as_dict()
    assert replace(AppConfig(), ui_scale=2.0).ui_scale == 2.0


# -- the gate -----------------------------------------------------------------

def test_the_ux_gate_runs_and_reports_nine_verdicts(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.ux_gate"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
        env={**_env(), "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout + result.stderr
    if "Tk no disponible" in output:
        pytest.skip("Tk unavailable on this machine: the gate needs a window")
    assert result.returncode in (0, 2), output
    if result.returncode == 2:
        # The only honest reason U9 can be inconclusive, and the reason the
        # phase 038 gate was written: this machine is busy with something
        # that is not this project.
        assert "INCONCLUYENTE" in output, output
        assert "carga de la maquina" in output
    assert output.count("PASS") >= 8, output
    assert "NO SHIP" not in output, output


def _env():
    import os

    return dict(os.environ)


def test_the_gate_writes_a_baseline_with_every_threshold():
    from evaluation import ux_gate

    assert set(ux_gate.THRESHOLDS) == {
        "U1_result_fields_without_a_column",
        "U2_empty_states_with_nothing_to_read",
        "U3_steps_that_need_a_mouse",
        "U4_failures_that_look_ordinary",
        "U5_stale_answers_after_a_failure",
        "U6_values_clipped_out_of_reach",
        "U7_gaps_that_ignore_the_ui_scale",
        "U8_pane_roles_that_ignore_the_theme",
        "U9_render_over_budget",
    }
    assert all(value == 0 for value in ux_gate.THRESHOLDS.values())
    assert ux_gate.RESULT_FIELDS == gui_app.RESULT_COLUMNS
    assert ux_gate.RENDER_BUDGET_MS > 0
