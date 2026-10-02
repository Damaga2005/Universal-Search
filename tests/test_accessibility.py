"""Phase 039: accessibility and interface, measured instead of promised.

The catalogue coverage check is the one that matters most over time: it is the
only thing that stops the interface quietly becoming untranslatable and
unauditable again the next time somebody adds a button.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import pytest

from universal_search.gui import accessibility, strings
from universal_search.gui.accessibility import (
    AA_BODY,
    AA_LARGE,
    CONTRAST_PAIRS,
    accessible_name,
    contrast_ratio,
    contrast_report,
    focus_order,
    focus_report,
    name_report_for,
)
from universal_search.gui.theme import DARK, LIGHT, THEMES, resolve


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(tmp_path, tk_guard):
    """The real search window over a small index."""
    from universal_search.appconfig import AppPaths
    from universal_search.gui.app import SearchWindow
    from universal_search.gui.services import SearchService
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    files = tmp_path / "files" / "electronica"
    files.mkdir(parents=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tmp_path / "files")
    service = SearchService(
        paths=AppPaths.discover(home=tmp_path / "home"), database_path=database.path
    )
    try:
        instance = SearchWindow(service=service)
    except tk.TclError as exc:
        # The known Tk flake on a loaded machine: the runtime sometimes cannot
        # read its own library during a fraction of a second. Skipping with the
        # reason is what docs/RELEASE.md prescribes; failing would make the
        # whole accessibility audit unusable on such a machine.
        pytest.skip(f"Tk runtime unavailable on this machine: {exc}")
    instance.results = []
    yield instance
    try:
        instance.destroy()
    except Exception:  # pragma: no cover - teardown best effort
        pass


# -- the string catalogue -----------------------------------------------------

def test_every_visible_string_comes_from_the_catalogue() -> None:
    """The acceptance criterion, as an executable check.

    An empty result means the interface is translatable and auditable as one
    list. A new button with its label written inline fails here.
    """
    missing = strings.untranslated_literals(ROOT)
    assert missing == {}, (
        "cadenas visibles escritas en el código fuera del catálogo: "
        + ", ".join(f"{literal!r} en {files}" for literal, files in missing.items())
    )


def test_the_catalogue_covers_every_gui_module() -> None:
    modules = strings.gui_modules(ROOT)
    assert len(modules) >= 5
    assert any(path.name == "app.py" for path in modules)
    assert any(path.name == "control_center.py" for path in modules)


def test_the_catalogue_has_no_duplicate_keys_or_values() -> None:
    keys = strings.keys()
    values = strings.values()
    assert len(keys) == len(set(keys))
    # The same wording twice under two keys is how a translator ends up
    # translating the same button twice and diverging.
    assert len(values) == len(set(values))


def test_catalogue_keys_say_what_a_string_is_not_what_it_says() -> None:
    """A key that embeds the text has to change when the text changes, which
    is the wrong dependency: renaming a button would then edit the key too."""
    for key in strings.keys():
        assert key == key.upper(), f"{key} no es un identificador"
        assert " " not in key, f"{key} contiene espacios"
        assert not any(character.islower() for character in key)
        assert key.replace("_", "").replace(".", "").isalnum()


def test_keys_are_grouped_so_the_catalogue_reads_in_screen_order() -> None:
    """``MENU.INDEXER.PAUSE`` already says where the string appears; a
    separate note field was removed in phase 039 as a second copy to forget."""
    for key in strings.keys():
        assert key.count(".") >= 1, f"{key} no dice en qué grupo aparece"


def test_a_missing_key_is_loud() -> None:
    """An empty label in a search window is exactly what the audit looks for,
    so a missing key must raise rather than render nothing."""
    with pytest.raises(strings.MissingString) as failure:
        strings.get("NO.EXISTE")
    assert "NO.EXISTE" in str(failure.value)


def test_placeholders_are_filled() -> None:
    assert strings.get("MENU.SELECTION.OPEN")
    rendered = strings.get("RESULTS.COUNT", count=7)
    assert "7" in rendered and "{" not in rendered


def test_a_missing_placeholder_is_reported_not_ignored() -> None:
    """``str.format`` would put a literal ``{count}`` on screen and say
    nothing, so the fields are checked before formatting."""
    with pytest.raises(strings.MissingString) as failure:
        strings.get("RESULTS.COUNT")
    assert "count" in str(failure.value)


def test_a_misspelled_field_is_reported() -> None:
    with pytest.raises(strings.MissingString) as failure:
        strings.get("RESULTS.COUNT", cont=3)
    assert "count" in str(failure.value)


def test_every_placeholder_is_one_a_caller_can_supply() -> None:
    for key in strings.keys():
        for name in strings.fields_of(key):
            assert name.isidentifier(), f"{key}.{name} no es un identificador"


def test_the_catalogue_is_data_not_code() -> None:
    """Importable without Tk, so a build step or a translation tool can read
    it on any machine."""
    assert "tkinter" not in Path(strings.__file__).read_text(encoding="utf-8")


# -- contrast ----------------------------------------------------------------

def test_contrast_ratio_matches_the_wcag_reference_points() -> None:
    # The two anchors the specification gives: black on white is 21:1, and a
    # colour against itself is 1:1.
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#3a7bd5", "#3a7bd5") == pytest.approx(1.0)


def test_contrast_is_symmetric() -> None:
    assert contrast_ratio("#1a5fb4", "#ffffff") == pytest.approx(
        contrast_ratio("#ffffff", "#1a5fb4")
    )


def test_every_colour_the_window_draws_is_checked() -> None:
    """A palette colour with no pair in the table is a colour nobody audited.

    Phase 039 found one: ``surface`` was declared in both palettes and drawn
    by no widget, so it was removed rather than given an invented requirement.
    """
    drawn = {
        field.name for field in LIGHT.__dataclass_fields__.values()
        if field.name not in {"name", "dark"}
    }
    used = set()
    for _name, foreground, background, _required in CONTRAST_PAIRS:
        used.add(foreground)
        used.add(background)
    assert drawn - used == set(), f"colores sin comprobar: {drawn - used}"
    assert "surface" not in LIGHT.__dataclass_fields__


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_every_theme_passes_wcag_aa(theme) -> None:
    failures = [
        f"{check.name} {check.ratio:.2f}:1 (necesita {check.required})"
        for check in contrast_report(theme)
        if not check.ok
    ]
    assert not failures, f"contraste insuficiente en {theme.name}: " + "; ".join(failures)


def test_body_text_needs_the_higher_level() -> None:
    body = [c for c in contrast_report(LIGHT) if c.required == AA_BODY]
    assert len(body) >= 6
    large = [c for c in contrast_report(LIGHT) if c.required == AA_LARGE]
    assert large, "there must be a large-text or non-text pair to check too"


def test_the_system_theme_resolves_to_one_of_the_two() -> None:
    resolved = resolve("system", prefer_dark=True)
    assert resolved.name == "dark"
    assert resolve("system", prefer_dark=False).name == "light"
    assert set(THEMES) == {"light", "dark"}


# -- keyboard reachability ---------------------------------------------------
#
# These need a real Tk runtime, so they are skipped with a reason where one
# cannot be created: the same `tk_guard` the GUI suites already use.

# -- error messages are visible as well as legible ----------------------------

def _drawn_colour(label) -> str:
    """The colour a widget is actually painted in.

    ``ttk`` answers ``cget("foreground")`` with a colour object rather than a
    string, so comparing it to a hex value directly fails for a reason that has
    nothing to do with the code under test.
    """
    value = label.cget("foreground")
    return str(value)


def test_an_error_is_not_drawn_like_an_ordinary_status_line(window) -> None:
    """Phase 039 found that a failure was rendered in exactly the same muted
    grey as "Listo — escribe para buscar", so an error a user needed to read
    looked like a message they could ignore. `danger` was in the palette and
    drawn nowhere.
    """
    window._set_status(strings.get("SEARCH.READY"))
    ordinary = _drawn_colour(window.status_label)
    window._set_status(strings.get("ERROR.SEARCH"), "error")
    failing = _drawn_colour(window.status_label)

    assert failing != ordinary, "an error must not look like ordinary status"
    assert failing == window.theme.danger


def test_a_warning_is_distinguished_from_an_error(window) -> None:
    window._set_status(strings.get("ERROR.SEARCH"), "error")
    error = _drawn_colour(window.status_label)
    window._set_status("pausado", "warning")
    warning = _drawn_colour(window.status_label)
    assert warning == window.theme.busy
    assert warning != error


def test_an_unknown_severity_falls_back_to_muted(window) -> None:
    window._set_status("algo", "nonsense")
    assert _drawn_colour(window.status_label) == window.theme.muted


def test_every_error_string_is_reported_as_an_error(window) -> None:
    """A failure the user must not miss has to be drawn as one. This walks the
    catalogue so a new error cannot be added without severity."""
    error_keys = [key for key in strings.keys() if key.startswith("ERROR.")]
    assert len(error_keys) >= 8
    for key in error_keys:
        window._set_status(strings.get(key), "error")
        assert _drawn_colour(window.status_label) == window.theme.danger, key


def test_the_whole_journey_works_without_a_mouse(window, monkeypatch) -> None:
    """The acceptance criterion as a journey, not a list: type, move down,
    open. Everything below goes through key events on the focused widget —
    no ``invoke()``, no ``event_generate("<Button-1>")`` anywhere.

    ``open`` is intercepted because actually launching a file in a test would
    open the user's PDF viewer; what is asserted is that the keyboard path
    *reached the open action with the right document selected*.
    """
    opened: list[str] = []

    # Intercepted at the platform seam, not on the window: the key binding
    # captured the bound method when the window was built, so replacing the
    # class attribute afterwards would leave the binding pointing at the
    # original. Opening the real file would launch the user's PDF viewer.
    monkeypatch.setattr(
        "universal_search.platforms.get_platform",
        lambda: type("Fake", (), {
            "open_path": staticmethod(lambda path: opened.append(str(path))),
            "reveal": staticmethod(lambda path: None),
        })(),
    )

    window.entry.focus_force()
    window.update_idletasks()
    window.entry.insert(0, "capacitor")
    window._on_query_changed()
    _drain(window)

    assert [result.name for result in window.results] == ["capacitor.md"]

    # Arrow down from the entry selects the first result.
    window.entry.focus_force()
    window.event_generate("<Down>")
    window.update()
    assert window._selected_index() == 0

    window.event_generate("<Return>")
    window.update()
    assert opened, "Enter did not reach the open action"
    assert opened[0].endswith("capacitor.md")


def _drain(window, attempts: int = 100) -> None:
    """Run the window's event loop until the search worker delivers."""
    for _ in range(attempts):
        if window.results:
            return
        window.pump(timeout=0.2)


def test_the_window_can_be_reached_with_the_keyboard_alone(window) -> None:
    """The acceptance criterion as an executable check: every interactive
    control is in the Tab ring, declared explicitly rather than left to a
    platform style default."""
    report = focus_report(window)
    assert report.unreachable == (), (
        "controles que el teclado no alcanza: " + ", ".join(report.unreachable)
    )
    assert report.reachable >= 4  # entry, three comboboxes, results pane, menubutton


def test_the_focus_ring_covers_the_whole_window(window) -> None:
    # Phase 041: the results pane is packed only when there is something in
    # it, so this asks the question in the state that matters — a window with
    # results. An empty pane is genuinely not a tab stop, and pretending
    # otherwise would make the ring a test of the implementation rather than
    # of the experience.
    from universal_search.index.search import SearchResult

    window._render([SearchResult(path=Path(r"C:\docs\notas.md"), name="notas.md",
                                 source="local", snippet="texto", rank=0.0)])
    ring = focus_order(window)
    roles = [type(widget).__name__ for widget in ring]
    assert roles[0] == "Entry", "typing must come first"
    assert "Treeview" in roles, "the results must be reachable without a mouse"


def test_focus_starts_in_the_query_box(window) -> None:
    """Typing must work the moment the window opens.

    ``focus_get()`` is the OS focus, which a headless or unfocused window does
    not report, so the check asks for focus first and skips with the reason if
    the platform still refuses: the ring's first element is asserted
    separately, and that part works everywhere.
    """
    window.entry.focus_force()
    window.update_idletasks()
    current = window.focus_get()
    if current is None:
        pytest.skip("this window has no OS focus (headless or unfocused session)")
    assert current is window.entry


def test_every_control_has_an_explicit_takefocus(window) -> None:
    """A control left to a platform default may or may not be reachable, and
    which depends on the style. Four controls were exactly like that before
    phase 039."""
    for widget in accessibility.reachable_widgets(window):
        assert str(widget.cget("takefocus")) == "1", (
            f"{type(widget).__name__} depende del foco por defecto de la "
            "plataforma; decláralo con takefocus=True"
        )


def test_every_control_has_a_name_a_screen_reader_could_say(window) -> None:
    report = name_report_for(window)
    assert report.ok, (
        "controles sin nombre accesible: " + ", ".join(report.unnamed)
    )


def test_a_control_with_no_text_has_no_invented_name(window) -> None:
    """A widget with neither its own text nor a declaration must report
    nothing, not fall back to its class name."""
    plain = tk.Label(window, text="")
    assert accessible_name(plain, window) == ""


def test_a_named_control_announces_what_it_is_for(window) -> None:
    """A screen reader that says "combobox (todos)" has told the user nothing.
    The name is what the control is *for*."""
    assert accessible_name(window.source_combo, window) == strings.get(
        "SEARCH.LABEL.SOURCE"
    )
    assert accessible_name(window.type_combo, window) == strings.get(
        "SEARCH.LABEL.TYPE"
    )
    assert accessible_name(window.context_combo, window) == strings.get(
        "SEARCH.LABEL.CONTEXT"
    )


def test_each_filter_gets_its_own_name(window) -> None:
    """Three labels share one parent frame, so a positional guess would name
    three different filters "Contexto:". The declaration is what keeps them
    apart, and this is the test that would catch it collapsing again."""
    names = {
        accessible_name(widget, window)
        for widget in (window.context_combo, window.source_combo, window.type_combo)
    }
    assert len(names) == 3


def test_names_come_from_the_catalogue_so_they_can_be_translated(window) -> None:
    for widget in accessibility.reachable_widgets(window):
        name = accessible_name(widget, window)
        assert name in set(strings.values()), (
            f"{type(widget).__name__} se anuncia como {name!r}, que no está "
            "en el catálogo"
        )


# -- the evidence gate --------------------------------------------------------
#
# Run in a subprocess, and the reason is worth stating: the gate builds a real
# window and destroys it, and a destroyed Tk interpreter leaves the process
# unable to create another ("Can't find a usable init.tcl"). The first version
# called `main()` in-process and quietly turned every later window test in this
# file into a skip. The gate is a command; it is exercised as one.

def test_the_accessibility_gate_is_green() -> None:
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-m", "evaluation.accessibility_gate"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    out = completed.stdout
    if "Tk no disponible" in out:
        pytest.skip("Tk runtime unavailable; the gate reported NOT RUN")
    assert completed.returncode == 0, out + completed.stderr
    assert "VEREDICTO: SHIP" in out
    assert out.count("PASS") >= 6
    assert "FAIL" not in out


def test_the_gate_declares_every_threshold() -> None:
    from evaluation import accessibility_gate

    assert set(accessibility_gate.THRESHOLDS) == {
        "T1_unreachable_controls",
        "T2_unnamed_controls",
        "T3_untranslated_literals",
        "T4_contrast_failures",
        "T5_undrawn_colours",
        "T6_error_looks_like_status",
    }
    assert all(value == 0 for value in accessibility_gate.THRESHOLDS.values()), (
        "every accessibility gate is a zero-tolerance gate"
    )