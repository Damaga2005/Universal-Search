"""Phase 041 evidence: the primary experience, measured rather than asserted.

Run from the repository root::

    python -m evaluation.ux_gate

Phase 039 measured whether the window could be *used*: a control in the Tab
ring, a name, a contrast ratio, a failure that looks like a failure. It never
asked whether the thing on screen made sense as a product. Those are different
questions and only the first one had an instrument, which is how forty phases
of measurable work could still leave a result list that flattened a filename, a
folder, a type, a source and a snippet into one string and then clipped it.

So each gate below answers a question a user would ask, with a number:

* **U1** the results pane gives each part of a result its own labelled column;
* **U2** when there is nothing to list, the largest area of the window says so;
* **U3** launch -> type -> results -> select -> open/reveal needs no mouse;
* **U4** a failure is drawn as a failure, on both error paths;
* **U5** a failed search leaves no stale answers behind;
* **U6** long names, paths and snippets are reachable rather than clipped;
* **U7** the UI scale reaches the gaps and not only the fonts;
* **U8** switching theme actually changes what the results pane draws;
* **U9** rendering a full page of results stays inside the interaction budget.

U9 is a timing claim, and a timing claim taken on a busy machine describes
somebody else's software. It reuses the load veto phase 038 wrote, and when the
machine is too busy to measure, the gate exits 2 (INCONCLUSIVE) instead of
passing or failing a number it does not believe.

Everything needs one real Tk runtime, and only one. Building a second window in
the same process is the known ``Can't find a usable init.tcl`` flake, so the
gate takes one window and measures everything on it.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.accessibility_gate import Verdict  # noqa: E402
from evaluation import perf_gate  # noqa: E402
from evaluation.perf_gate import load_verdict, measure_load, os_cpu_load  # noqa: E402
from universal_search.gui import app as gui_app  # noqa: E402
from universal_search.gui import strings, theme as theme_module  # noqa: E402
from universal_search.gui.accessibility import focus_order  # noqa: E402

THRESHOLDS = {
    "U1_result_fields_without_a_column": 0,
    "U2_empty_states_with_nothing_to_read": 0,
    "U3_steps_that_need_a_mouse": 0,
    "U4_failures_that_look_ordinary": 0,
    "U5_stale_answers_after_a_failure": 0,
    "U6_values_clipped_out_of_reach": 0,
    "U7_gaps_that_ignore_the_ui_scale": 0,
    "U8_pane_roles_that_ignore_the_theme": 0,
    "U9_render_over_budget": 0,
}

GATE_LINES = {
    "U1_result_fields_without_a_column": "U1 partes de un resultado sin columna",
    "U2_empty_states_with_nothing_to_read": "U2 estados vacios sin nada que leer",
    "U3_steps_that_need_a_mouse": "U3 pasos que necesitan raton",
    "U4_failures_that_look_ordinary": "U4 fallos que parecen un estado normal",
    "U5_stale_answers_after_a_failure": "U5 respuestas viejas tras un fallo",
    "U6_values_clipped_out_of_reach": "U6 valores recortados e inalcanzables",
    "U7_gaps_that_ignore_the_ui_scale": "U7 huecos que ignoran la escala",
    "U8_pane_roles_that_ignore_the_theme": "U8 partes del panel que ignoran el tema",
    "U9_render_over_budget": "U9 relleno del panel por encima del presupuesto",
}

# How long filling the results pane may take. The search itself runs on a
# worker thread; this is the part on the UI thread, and it is what decides
# whether results feel like they arrived or like the window stalled.
RENDER_BUDGET_MS = 200.0
RENDER_ROWS = gui_app.DEFAULT_LIMIT

# The five things a search result is, and the column each one must occupy.
RESULT_FIELDS = ("name", "folder", "kind", "source", "snippet")

# launch -> type -> results -> select -> open/reveal, and the pieces a mouse
# would otherwise be needed for. A key of ``None`` means "the keyboard starts
# here", which is a claim about the Tab ring rather than about a binding.
FLOW = (
    ("escribir", "entry", None),
    ("ver resultados", "tree", "<Down>"),
    ("abrir", "tree", "<Return>"),
    ("mostrar en el explorador", "tree", "<Control-Return>"),
    ("borrar la consulta", "entry", "<Escape>"),
    ("copiar rutas", "tree", "<Control-c>"),
)

# The roles the results pane draws, so U8 can ask whether a theme switch would
# be visible in each of them. These are palette fields rather than ttk option
# names on purpose: `background` and `fieldbackground` are two ttk options fed
# from one colour, so counting both would raise the number without asking
# anything new.
PANE_ROLES = (
    ("fondo del panel", "background"),
    ("texto de las filas", "foreground"),
    ("texto atenuado", "muted"),
    ("fila seleccionada", "selection_background"),
    ("texto seleccionado", "selection_foreground"),
    ("encabezados de columna", "accent"),
)

LONG_NAME = (
    "informe_definitivo_del_proyecto_de_grado_con_un_nombre_deliberadamente"
    "_largo_para_probar_que_no_desaparece.md"
)
LONG_SNIPPET = (
    "el capacitor de cien microfaradios con la tolerancia marcada en la hoja "
    "de datos " * 8
).strip()


def _build_window(tmp: Path):
    """A real search window over a small index, or ``None`` with the reason."""
    try:
        import tkinter as tk
    except ImportError as exc:  # pragma: no cover - Tk ships with CPython
        return None, f"sin tkinter: {exc}"

    from universal_search.appconfig import AppPaths
    from universal_search.gui.app import SearchWindow
    from universal_search.gui.services import SearchService
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    files = tmp / "files" / "electronica"
    files.mkdir(parents=True, exist_ok=True)
    (files / "capacitor.md").write_text(
        "notas sobre el capacitor de 100 uF", encoding="utf-8"
    )
    (files / "notas.md").write_text("apuntes de clase", encoding="utf-8")
    database = SearchDatabase(tmp / "index.db")
    Indexer(database).index_root(tmp / "files")
    service = SearchService(
        paths=AppPaths.discover(home=tmp / "home"), database_path=database.path
    )
    try:
        return SearchWindow(service=service), None
    except tk.TclError as exc:
        return None, f"Tk no disponible en esta máquina: {exc}"


def _synthetic(index: int, name: str = "documento.md", snippet: str | None = "texto"):
    """One result that does not need to exist on disk."""
    from universal_search.index.search import SearchResult

    return SearchResult(
        path=Path(r"C:\Users\alguien\documentos\electronica") / name,
        name=name,
        source="local",
        snippet=snippet,
        rank=0.0,
        document_id=f"ux-{index}",
    )


def _measure_window(window) -> tuple[dict[str, float], dict[str, str]]:
    """Every gate, on the one window this process is allowed to open."""
    from tkinter import ttk

    measured: dict[str, float] = {}
    details: dict[str, str] = {}

    # -- U1: one labelled column per part of a result -----------------------
    headings = {
        field: str(window.tree.heading(field, "text") or "").strip()
        for field in RESULT_FIELDS
    }
    missing = [
        field for field in RESULT_FIELDS
        if field not in gui_app.RESULT_COLUMNS or not headings[field]
    ]
    duplicated = sorted(
        {text for text in headings.values() if text and list(headings.values()).count(text) > 1}
    )
    missing += [f"encabezado duplicado: {text}" for text in duplicated]
    measured["U1_result_fields_without_a_column"] = len(missing)
    details["U1_result_fields_without_a_column"] = (
        f"{len(gui_app.RESULT_COLUMNS)} columnas, {len(RESULT_FIELDS)} partes, "
        f"encabezados {sorted(set(headings.values()))}"
        if not missing else f"sin columna: {missing}"
    )

    # -- U2: the empty state says something ----------------------------------
    window.query_var.set("noexistenadaquienadie")
    window._render([])
    shown = window.empty_frame.winfo_manager() != ""
    title = window.empty_title.cget("text").strip()
    hint = window.empty_hint.cget("text").strip()
    blank = int(not shown or not title or not hint)
    measured["U2_empty_states_with_nothing_to_read"] = blank
    details["U2_empty_states_with_nothing_to_read"] = (
        f"«{title}» / «{hint[:52]}»"
        if not blank else "el area de resultados no muestra ningun texto"
    )

    # -- U3: the flow needs no mouse -----------------------------------------
    window.query_var.set("documento")
    window._render([_synthetic(0)])
    ring = focus_order(window)
    widgets = {"entry": window.entry, "tree": window.tree}
    mouse_only = []
    for label, attribute, key in FLOW:
        widget = widgets[attribute]
        if key is None:
            if not ring or ring[0] is not widget:
                mouse_only.append(label)
        elif not widget.bind(key):
            mouse_only.append(label)
    measured["U3_steps_that_need_a_mouse"] = len(mouse_only)
    details["U3_steps_that_need_a_mouse"] = (
        f"{len(FLOW)} pasos del recorrido; anillo "
        f"{[type(w).__name__ for w in ring]}"
        if not mouse_only else f"necesitan raton: {mouse_only}"
    )

    # -- U4: both error paths are drawn as failures -------------------------
    window._set_status(strings.get("SEARCH.READY"))
    ordinary = str(window.status_label.cget("foreground"))
    quiet = []
    window._apply_search([], "operator 'AND' needs a term after it", None)
    if str(window.status_label.cget("foreground")) == ordinary:
        quiet.append("consulta invalida")
    window._apply_search([], None, "RuntimeError: db locked")
    if str(window.status_label.cget("foreground")) == ordinary:
        quiet.append("fallo de busqueda")
    measured["U4_failures_that_look_ordinary"] = len(quiet)
    details["U4_failures_that_look_ordinary"] = (
        f"estado normal {ordinary}, fallo {window.theme.danger}"
        if not quiet else f"parecen normales: {quiet}"
    )

    # -- U5: a failure leaves nothing stale ----------------------------------
    stale = []
    for label, error, failure in (
        ("fallo de busqueda", None, "RuntimeError: db locked"),
        ("consulta invalida", "operator 'AND' needs a term after it", None),
    ):
        window.query_var.set("documento")
        window._render([_synthetic(0), _synthetic(1, "otro.md")])
        window._apply_search([], error, failure)
        if len(window.tree.get_children()) or window.results:
            stale.append(label)
    measured["U5_stale_answers_after_a_failure"] = len(stale)
    details["U5_stale_answers_after_a_failure"] = (
        "las dos rutas de fallo vacian el panel"
        if not stale else f"quedan resultados de antes: {stale}"
    )

    # -- U6: nothing important is clipped away ------------------------------
    window.query_var.set("largo")
    long_result = _synthetic(0, name=LONG_NAME, snippet=LONG_SNIPPET)
    window._render([long_result])
    detail_path = window.preview_path["text"]
    detail_snippet = window.preview_snippet["text"]
    clipped = []
    if long_result.name not in window.preview["text"]:
        clipped.append("nombre")
    if str(long_result.path) not in detail_path:
        clipped.append("ruta")
    if len(detail_snippet) < len(LONG_SNIPPET) - 2:
        clipped.append("coincidencia")
    row_snippet = window.tree.item("0", "values")[-1]
    if len(row_snippet) > theme_module.SNIPPET_CHARS:
        clipped.append("la fila no acota la coincidencia")
    measured["U6_values_clipped_out_of_reach"] = len(clipped)
    details["U6_values_clipped_out_of_reach"] = (
        f"detalle: {len(detail_path)} caracteres de ruta, "
        f"{len(detail_snippet)} de coincidencia; la fila acota a "
        f"{len(row_snippet)} (presupuesto {theme_module.SNIPPET_CHARS})"
        if not clipped else f"recortados: {clipped}"
    )

    # -- U7: the scale reaches the gaps, not only the fonts ------------------
    style = ttk.Style(window)
    unscaled = int(
        theme_module.spacing(1.0) == theme_module.spacing(2.0)
        or str(style.lookup(gui_app.RESULT_STYLE, "rowheight"))
        != str(window.spacing.row_height)
    )
    measured["U7_gaps_that_ignore_the_ui_scale"] = unscaled
    details["U7_gaps_that_ignore_the_ui_scale"] = (
        f"escala {window.ui_scale}: fila {window.spacing.row_height} px, "
        f"padding {window.spacing.pad} px, tipografia "
        f"{window.fonts['entry'][1]} pt"
        if not unscaled else "los huecos no siguen a la escala"
    )

    # -- U8: a theme switch would be visible in every role the pane draws ----
    blind = [
        role for role, field in PANE_ROLES
        if getattr(theme_module.LIGHT, field) == getattr(theme_module.DARK, field)
    ]
    measured["U8_pane_roles_that_ignore_the_theme"] = len(blind)
    details["U8_pane_roles_that_ignore_the_theme"] = (
        f"{len(PANE_ROLES)} roles del panel con color propio en cada tema"
        if not blind else f"mismo color en claro y oscuro: {blind}"
    )

    # -- U9: a full page of rows, on the UI thread, inside the budget -------
    # The load check runs first and on purpose: phase 038 learned that a
    # latency number taken while the machine is busy describes other software.
    load = measure_load()
    # Phase 050. See perf_gate.require_conclusive: a shared machine gets
    # INCONCLUYENTE, a CI runner gets a failure.
    verdict_load = load_verdict(load, reference=None, os_load=os_cpu_load())
    page = [_synthetic(i, f"documento-{i}.md", "una coincidencia de ejemplo")
            for i in range(RENDER_ROWS)]
    # One warm-up pass, not to flatter the number but to measure the right
    # thing. The first `_render` on a fresh window pays for the whole first
    # layout, which no user ever experiences: what a user feels is the second
    # search, when the window already exists and only the rows change.
    window._render(page)
    window.update_idletasks()
    started = time.perf_counter()
    window._render(page)
    window.update_idletasks()
    render_ms = (time.perf_counter() - started) * 1000
    drawn = len(window.tree.get_children())
    measured["U9_render_over_budget"] = max(0.0, render_ms - RENDER_BUDGET_MS)
    details["U9_render_over_budget"] = (
        f"{drawn} filas en {render_ms:.1f} ms "
        f"(presupuesto {RENDER_BUDGET_MS:.0f} ms)"
    )
    measured["_render_ms"] = render_ms
    details["_load"] = verdict_load.detail
    measured["_load_ok"] = 1.0 if verdict_load.conclusive else 0.0

    return measured, details


def main() -> int:
    # Phase 050. Read once, here, and not in `_measure`: the helper
    # removes the argument on the first call, so a second call in
    # another function would silently return False.
    strict = perf_gate.require_conclusive()
    # The gate prints the interface's own strings, some of which contain
    # characters the cp1252 console cannot encode. Piping the output -- how CI
    # and the test suite run it -- is what turns that into a crash halfway
    # through the report. Same reason, and same fix, as the phase 039 gate.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-041-"))
    window, reason = _build_window(workspace)
    if window is None:
        measured = {gate: 1.0 for gate in THRESHOLDS}
        details = {gate: reason for gate in THRESHOLDS}
        render_ms, load_note = 0.0, reason
        load_ok = False
    else:
        try:
            measured, details = _measure_window(window)
        finally:
            try:
                window.destroy()
            except Exception:  # pragma: no cover - teardown best effort
                pass
        render_ms = measured.pop("_render_ms", 0.0)
        load_note = details.pop("_load", "")
        load_ok = bool(measured.pop("_load_ok", 0.0))

    verdicts = [
        Verdict(
            gate=GATE_LINES[gate],
            measured=float(measured.get(gate, 1)),
            threshold=float(threshold),
            passed=measured.get(gate, 1) <= threshold,
            detail=details.get(gate, "no medido"),
        )
        for gate, threshold in THRESHOLDS.items()
    ]
    # U9 is the only gate that may legitimately end in "I do not know".
    timing = verdicts[-1]
    if not load_ok:
        timing.detail = (
            f"INCONCLUYENTE: {load_note}; {timing.detail}"
        )
    timing.passed = timing.passed and load_ok

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 041 (experiencia de producto)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("carga de la maquina antes de medir el relleno:")
    print(f"  {load_note}")

    failed = [v for v in verdicts if not v.passed]
    payload = {
        "phase": "041",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "window_checked": window is not None,
        "window_reason": reason,
        "render_ms": round(render_ms, 2),
        "render_budget_ms": RENDER_BUDGET_MS,
        "load_ok": load_ok,
    }
    out = ROOT / "evaluation" / "ux_baseline.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")

    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    if len(failed) == 1 and timing in failed and not load_ok:
        return perf_gate.veto_exit(strict)
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
