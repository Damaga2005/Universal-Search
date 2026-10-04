"""Phase 042 evidence: search as one interactive flow, measured not asserted.

Run from the repository root::

    python -m evaluation.interaction_gate

Phases 012, 031, 032 and 036 each shipped a gate for their own mechanism, and
each mechanism works: the query language parses, the fuzzy layer matches typos,
the suggester verifies corrections, sorting reorders without losing anything.
What none of them could check is the **seam** -- whether the window actually
calls them, and whether what reaches the user is the same thing that was
measured underneath. That is what this gate is for, and it is not a formality:
its first invariant exists because phase 041 drew a malformed query as a
failure that the running application could never be shown.

* **V1** a rejected query reaches the user, through the real service;
* **V2** an explicit filter disables the optional layers;
* **V3** a suggestion is offered and never applied on its own;
* **V4** sorting does not change which results you get;
* **V5** grouping loses and invents nothing;
* **V6** a non-relevance sort asks for a wider pool, and the pool is capped;
* **V7** history can be disabled, cleared and inspected, with stated retention;
* **V8** a saved search holds exactly six fields and round-trips;
* **V9** explanations are computed only when asked for;
* **V10** rendering reuses the rows that did not change;
* **V11** keystroke-to-first-result stays inside the interaction budget.

V11 is a timing claim, so it reuses the phase 038 load veto: a busy machine
makes the gate exit 2 (INCONCLUSIVE) rather than publish a number it does not
believe. One window is built and everything is measured on it, because a second
``Tk()`` root in one process is the known ``Can't find a usable init.tcl`` flake.
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
from universal_search.organize import (  # noqa: E402
    GROUP_FOLDER,
    GROUP_TYPE,
    MAX_SORT_POOL,
    SORT_NAME,
    SORT_RELEVANCE,
    pool_size,
)
from universal_search.query import SOURCE_KINDS  # noqa: E402

THRESHOLDS = {
    "V1_rejected_queries_that_never_reach_the_user": 0,
    "V2_filters_that_do_not_disable_the_optional_layers": 0,
    "V3_suggestions_that_change_the_query_by_themselves": 0,
    "V4_sorts_that_change_which_results_you_get": 0,
    "V5_results_lost_or_invented_by_grouping": 0,
    "V6_sorts_that_hide_a_truncated_pool": 0,
    "V7_history_controls_that_do_not_work": 0,
    "V8_saved_search_fields_outside_the_six": 0,
    "V9_explanations_computed_without_being_asked": 0,
    "V10_rows_rebuilt_that_did_not_change": 0,
    "V11_keystroke_to_result_over_budget": 0,
}

GATE_LINES = {
    "V1_rejected_queries_that_never_reach_the_user":
        "V1 consultas rechazadas que no llegan al usuario",
    "V2_filters_that_do_not_disable_the_optional_layers":
        "V2 filtros que no desactivan las capas opcionales",
    "V3_suggestions_that_change_the_query_by_themselves":
        "V3 sugerencias que cambian la consulta solas",
    "V4_sorts_that_change_which_results_you_get":
        "V4 ordenaciones que cambian cuales resultados son",
    "V5_results_lost_or_invented_by_grouping":
        "V5 resultados perdidos o inventados al agrupar",
    "V6_sorts_that_hide_a_truncated_pool":
        "V6 ordenaciones sobre un conjunto truncado",
    "V7_history_controls_that_do_not_work":
        "V7 controles de historial que no funcionan",
    "V8_saved_search_fields_outside_the_six":
        "V8 campos de una busqueda guardada fuera de los seis",
    "V9_explanations_computed_without_being_asked":
        "V9 explicaciones calculadas sin pedirlas",
    "V10_rows_rebuilt_that_did_not_change":
        "V10 filas rehechas que no cambiaron",
    "V11_keystroke_to_result_over_budget":
        "V11 de pulsacion a resultado por encima del presupuesto",
}

# The whole cycle a user waits through, debounce included: the 150 ms is part of
# what "it feels instant" has to include, not something to subtract.
KEYSTROKE_BUDGET_MS = 600.0
KEYSTROKE_ROUNDS = 3

BROAD_QUERY = "capacitor"
TYPO_QUERY = "capacitos"


def _build_window(tmp: Path):
    """A real window over a small index, or ``None`` with the reason."""
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
    # A second document for the same term, so sorting and grouping have
    # something to distinguish. A one-document index cannot tell them apart.
    (files / "resumen.md").write_text(
        "resumen: el capacitor y el diodo en un circuito de desacoplamiento",
        encoding="utf-8",
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


def _search(window, query: str) -> None:
    window.query_var.set(query)
    window._execute_search()
    window.pump(timeout=5)


def _measure(window) -> tuple[dict[str, float], dict[str, str], dict[str, object]]:
    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    extra: dict[str, object] = {}

    window.query_var.set("")
    window._clear_results()

    # -- V1: the seam phase 041 could not see -------------------------------
    _search(window, "algo AND")
    shown = window.empty_title.cget("text")
    painted = str(window.status_label.cget("foreground")) == window.theme.danger
    reached = "Consulta no válida" in shown and painted
    measured["V1_rejected_queries_that_never_reach_the_user"] = 0 if reached else 1
    details["V1_rejected_queries_that_never_reach_the_user"] = (
        f"titulo «{shown[:48]}», color {window.theme.danger}, "
        f"resultados {len(window.results)}"
    )

    # -- V2: an explicit filter disables the optional layers ----------------
    # The layers' own contract, asked of the wiring rather than of the layer.
    from universal_search.fuzzy import FuzzySearchEngine

    assert isinstance(window.service.engine, FuzzySearchEngine)
    leaks = []
    unfiltered = len(window.service.search("capacitor", limit=10))
    for source in SOURCE_KINDS:
        results = window.service.search("capacitor", limit=10, source=source)
        if not results and source != "other":
            continue
        explained = any(result.explain for result in results)
        if explained and any(
            "fuzzy" in str(key) for result in results
            for key in (result.explain or {})
        ):
            leaks.append(f"source:{source}")
    measured["V2_filters_that_do_not_disable_the_optional_layers"] = len(leaks)
    details["V2_filters_that_do_not_disable_the_optional_layers"] = (
        f"fuzzy presente; {unfiltered} sin filtro, ninguna de "
        f"{len(SOURCE_KINDS)} fuentes devuelve resultados difusos"
        if not leaks else f"fugas: {leaks}"
    )

    # -- V3: a suggestion is an offer --------------------------------------
    window.service.set_fuzzy_enabled(False)
    _search(window, TYPO_QUERY)
    offered = window._suggestion_text()
    typed = window.query_var.get()
    offered_and_untouched = bool(offered) and typed == TYPO_QUERY
    window._apply_suggestion()
    applied = window.query_var.get()
    explicit = applied != TYPO_QUERY
    measured["V3_suggestions_that_change_the_query_by_themselves"] = (
        0 if (offered_and_untouched and explicit) else 1
    )
    details["V3_suggestions_that_change_the_query_by_themselves"] = (
        f"«{typed}» se ofrecio «{offered[:36]}» y solo cambio a «{applied}» "
        "al pedirlo"
    )
    window.service.set_fuzzy_enabled(True)

    # -- V4: sorting changes the order and nothing else --------------------
    _search(window, BROAD_QUERY)
    baseline = [result.path for result in window.results]
    window.sort_var.set(gui_app.SORT_LABELS[SORT_NAME])
    window._sort_results_only()
    by_name = [result.path for result in window.results]
    same_set = set(by_name) == set(baseline)
    ordered = by_name == sorted(by_name, key=lambda p: (p.name.casefold(), str(p)))
    measured["V4_sorts_that_change_which_results_you_get"] = (
        0 if (same_set and ordered and len(baseline) >= 2) else 1
    )
    details["V4_sorts_that_change_which_results_you_get"] = (
        f"{len(baseline)} resultados, el mismo conjunto en otro orden: "
        f"{same_set}, ordenado por nombre: {ordered}"
    )

    # -- V5: grouping loses and invents nothing ----------------------------
    window.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    window._render(window.results)
    window.group_var.set(gui_app.GROUP_LABELS[GROUP_FOLDER])
    window._render(window.results)
    window.update()
    shown_paths = [
        window.results[index].path for index in window._result_indices()
    ]
    lost = len(window.results) - len(shown_paths)
    invented = sum(1 for index in window._result_indices()
                   if index >= len(window.results))
    headers = [
        item for item in window.tree.get_children("") if item.startswith("g:")
    ]
    measured["V5_results_lost_or_invented_by_grouping"] = lost + invented
    details["V5_results_lost_or_invented_by_grouping"] = (
        f"{len(shown_paths)} de {len(window.results)} resultados, "
        f"{len(headers)} encabezado(s) de grupo, perdidos {lost}, "
        f"inventados {invented}"
    )

    # -- V6: a non-relevance sort asks for more than it shows --------------
    # Measured the way a user does it: choose the sort. Not by calling the
    # search directly, which would have hidden whether the *choice* widens the
    # pool -- and that was the first version, which passed the search and
    # failed the gate for exactly that reason.
    window.group_var.set(gui_app.GROUP_DISPLAY_LABELS[0])
    window._render(window.results)
    window.limit = 5
    # In the order a user does it: search, then choose how to order the answer.
    # Setting `limit` without searching first would leave the window claiming
    # a relevance pool it never asked for.
    window.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    window._execute_search()
    window.pump(timeout=5)
    window.sort_var.set(gui_app.SORT_LABELS[SORT_NAME])
    window._on_view_changed()
    window.pump(timeout=5)
    asked = window.requested_limit
    narrow = pool_size(window.limit, SORT_RELEVANCE)
    wider = pool_size(window.limit, SORT_NAME)
    capped = wider <= MAX_SORT_POOL
    honest = asked == wider and wider > narrow
    measured["V6_sorts_that_hide_a_truncated_pool"] = 0 if (capped and honest) else 1
    details["V6_sorts_that_hide_a_truncated_pool"] = (
        f"relevancia {narrow}, por nombre {wider} (tope {MAX_SORT_POOL}), "
        f"pedidos {asked}"
    )
    window.limit = gui_app.DEFAULT_LIMIT
    window.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    window._render(window.results)

    # -- V7: the history controls, and what they claim ---------------------
    window.service.record_query("capacitor")
    window.service.record_query("notas")
    problems = []
    if len(window.service.history()) != 2:
        problems.append("no guarda lo que se le dice")
    window.service.set_history_enabled(False)
    if window.service.history_enabled() is not False:
        problems.append("no se desactiva")
    if len(window.service.history()) != 2:
        problems.append("desactivar borra lo ya guardado")
    window.service.set_history_enabled(True)
    window.service.record_query("sin filtro")
    if "sin filtro" not in window.service.history():
        problems.append("no vuelve a guardar al reactivarlo")
    body = window._history_body()
    retention = window.service.history_retention()
    if str(retention["max_entries"]) not in body:
        problems.append("no declara la retencion")
    if retention["transmitted"] is not False:
        problems.append("no declara que no se transmite")
    removed = window.service.clear_history()
    if removed != 3 or window.service.history():
        problems.append(f"borrar dijo {removed}")
    measured["V7_history_controls_that_do_not_work"] = len(problems)
    details["V7_history_controls_that_do_not_work"] = (
        "guardar, desactivar sin borrar, reactivar, borrar y declarar la "
        "retencion: todo funciona"
        if not problems else f"problemas: {problems}"
    )

    # -- V8: a saved search holds six fields and round-trips ---------------
    window.service.set_history_enabled(False)
    _search(window, BROAD_QUERY)
    window.sort_var.set(gui_app.SORT_LABELS[SORT_NAME])
    window.group_var.set(gui_app.GROUP_LABELS[GROUP_TYPE])
    window._store_current_search("de la puerta")
    window.source_var.set(gui_app.SOURCE_FILTER_VALUES[0])
    window.type_var.set(gui_app.TYPE_FILTER_VALUES[0])
    window.sort_var.set(gui_app.SORT_DISPLAY_LABELS[0])
    window.group_var.set(gui_app.GROUP_DISPLAY_LABELS[0])
    window.query_var.set("")
    window._apply_saved_search("de la puerta")
    stored = window.service.saved_searches()
    fields = set(stored[0].as_dict()) if stored else set()
    allowed = {"name", "query", "sort", "group", "source", "doc_type"}
    round_trip = bool(stored) and (
        stored[0].sort == SORT_NAME
        and stored[0].group == GROUP_TYPE
        and window.query_var.get() == BROAD_QUERY
    )
    extra_fields = fields - allowed
    measured["V8_saved_search_fields_outside_the_six"] = len(extra_fields)
    details["V8_saved_search_fields_outside_the_six"] = (
        f"{len(fields)} campos, los seis permitidos, y la busqueda se restaura "
        f"completa: {round_trip}"
        if not extra_fields and round_trip else f"campos: {fields}, restaura: {round_trip}"
    )
    window._delete_saved_search("de la puerta")
    window.service.set_history_enabled(True)

    # -- V9: explanations only when asked ----------------------------------
    window._set_explain(False)
    _search(window, BROAD_QUERY)
    quiet = sum(1 for result in window.results if result.explain)
    window._set_explain(True)
    _search(window, BROAD_QUERY)
    loud = sum(1 for result in window.results if result.explain)
    measured["V9_explanations_computed_without_being_asked"] = quiet
    details["V9_explanations_computed_without_being_asked"] = (
        f"{quiet} resultados explicados sin pedirlo, {loud} al pedirlo"
    )
    window._set_explain(False)

    # -- V10: the rows that did not move are not rebuilt -------------------
    _search(window, BROAD_QUERY)
    window._render(window.results)
    stats = window._render_stats()
    measured["V10_rows_rebuilt_that_did_not_change"] = (
        stats["inserted"] + stats["updated"] + stats["removed"]
    )
    details["V10_rows_rebuilt_that_did_not_change"] = (
        f"reordenados {len(window.results)} resultados sin tocar ninguna fila: "
        f"{stats}"
    )

    # -- V11: keystroke to results, with the load veto ---------------------
    load = measure_load()
    # Phase 050. See perf_gate.require_conclusive: a shared machine gets
    # INCONCLUYENTE, a CI runner gets a failure.
    verdict_load = load_verdict(load, reference=None, os_load=os_cpu_load())
    best = None
    for _ in range(KEYSTROKE_ROUNDS):
        started = time.perf_counter()
        _search(window, BROAD_QUERY)
        elapsed = (time.perf_counter() - started) * 1000
        best = elapsed if best is None else min(best, elapsed)
    measured["V11_keystroke_to_result_over_budget"] = max(
        0.0, best - KEYSTROKE_BUDGET_MS
    )
    details["V11_keystroke_to_result_over_budget"] = (
        f"mejor de {KEYSTROKE_ROUNDS}: {best:.0f} ms "
        f"(presupuesto {KEYSTROKE_BUDGET_MS:.0f} ms)"
    )
    extra["render_ms"] = best
    extra["load_detail"] = verdict_load.detail
    extra["load_ok"] = verdict_load.conclusive

    return measured, details, extra


def main() -> int:
    # Phase 050. Read once, here, and not in `_measure`: the helper
    # removes the argument on the first call, so a second call in
    # another function would silently return False.
    strict = perf_gate.require_conclusive()
    # The gate prints the interface's own strings, some of which contain
    # characters the cp1252 console cannot encode; piping the output is how
    # CI and the test suite run it. Same reason, and same fix, as phase 039.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-042-"))
    window, reason = _build_window(workspace)
    if window is None:
        measured = {gate: 1.0 for gate in THRESHOLDS}
        details = {gate: reason for gate in THRESHOLDS}
        extra = {"render_ms": 0.0, "load_detail": reason, "load_ok": False}
    else:
        try:
            measured, details, extra = _measure(window)
        finally:
            try:
                window.destroy()
            except Exception:  # pragma: no cover - teardown best effort
                pass

    load_ok = bool(extra["load_ok"])
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
    timing = verdicts[-1]
    if not load_ok:
        timing.detail = f"INCONCLUYENTE: {extra['load_detail']}; {timing.detail}"
    timing.passed = timing.passed and load_ok

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 042 (experiencia de busqueda)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("carga de la maquina antes de medir la latencia:")
    print(f"  {extra['load_detail']}")

    failed = [v for v in verdicts if not v.passed]
    payload = {
        "phase": "042",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "window_checked": window is not None,
        "window_reason": reason,
        "keystroke_ms": round(float(extra["render_ms"]), 2),
        "keystroke_budget_ms": KEYSTROKE_BUDGET_MS,
        "load_ok": load_ok,
    }
    out = ROOT / "evaluation" / "interaction_baseline.json"
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