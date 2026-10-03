"""Phase 044 evidence: does local learning earn its place, and what does it cost?

Run from the repository root::

    python -m evaluation.learning_gate

Eleven invariants over a *synthetic* interaction history. No profile, no
embedding, no network call; a user is a list of
:class:`~evaluation.learning.UsageEvent` written by hand, and every measurement
runs against a fixed clock so the whole thing is reproducible.

* **L1** cold start: with no history, learning returns the baseline's answer;
* **L2** one accidental open also returns the baseline's answer;
* **L3** the same history twice gives the same order;
* **L4** forgetting: an aged history returns to the baseline;
* **L5** bounded: learning's share of the score is at most ``recency``'s;
* **L6** an exact-filename match is never boosted, whatever the history;
* **L7** and learning cannot overtake one -- measured against a rival built to
  be as close to it as the corpus allows;
* **L8** an explicit filter still wins over a learned preference;
* **L9** benefit: across every (query, candidate) pair in the corpus, does
  learning actually promote anything?
* **L10** no regression: with no history, MRR is unchanged and not one score
  moves;
* **L11** every material change is explainable: a boosted result says why.

L9 is the one that decides whether anything ships. A signal that is bounded,
reversible and explainable and does not help is still not worth having, and a
signal that helps by breaking L10 is worse than no signal.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation import corpus as corpus_module  # noqa: E402
from evaluation import learning  # noqa: E402
from evaluation.accessibility_gate import Verdict  # noqa: E402
from universal_search import learn  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402

THRESHOLDS = {
    "L1_cold_start_differences": 0,
    "L2_single_open_differences": 0,
    "L3_nondeterministic_runs": 0,
    "L4_histories_that_did_not_forget": 0,
    "L5_learning_stronger_than_recency": 0,
    "L6_exact_matches_boosted": 0,
    "L7_exact_matches_overturned_by_learning": 0,
    "L8_filters_overridden": 0,
    "L9_workflows_learning_did_not_help": 0,
    "L10_mrr_regression": 0.0,
    "L11_boosts_without_an_explanation": 0,
}

GATE_LINES = {
    "L1_cold_start_differences": "L1 diferencias en arranque en frio",
    "L2_single_open_differences": "L2 diferencias con una sola apertura",
    "L3_nondeterministic_runs": "L3 ejecuciones no deterministas",
    "L4_histories_that_did_not_forget": "L4 historiales que no olvidaron",
    "L5_learning_stronger_than_recency": "L5 aprendizaje mas fuerte que la recencia",
    "L6_exact_matches_boosted": "L6 coincidencias exactas impulsadas",
    "L7_exact_matches_overturned_by_learning": "L7 coincidencias exactas derrotadas",
    "L8_filters_overridden": "L8 filtros anulados por una preferencia",
    "L9_workflows_learning_did_not_help": "L9 flujos que el aprendizaje no ayudo",
    "L10_mrr_regression": "L10 regresion de MRR sin historial",
    "L11_boosts_without_an_explanation": "L11 impulsos sin explicacion",
}

# The corpus query the repeated-workflow measurements use, and the document a
# synthetic user keeps opening for it. Chosen from the corpus rather than
# invented, so every measurement is about a real document with real rivals.
WORKFLOW_QUERY = "bjt"
WORKFLOW_PATH = "bjt_punto_operacion.md"

# L7's controlled pair. The corpus has no query that both matches a file name
# exactly *and* has a close rival, so the closest honest experiment is built
# here: two files, the same word in both, one named exactly like the query.
DOMINANCE_QUERY = "factura"
DOMINANCE_EXACT = "factura.md"
DOMINANCE_RIVAL = "factura_detalle.md"


def _write_dominance_pair(root: Path) -> Path:
    """Two files one word apart in naming and near-identical in everything else.

    The rival repeats the word twice as often, which is exactly the situation
    where a weak extra signal could flip the order -- so if the exact match
    survives here, the margin it survives by is a real number.
    """
    root.mkdir(parents=True, exist_ok=True)
    body = " ".join(["factura", "del", "cliente", "pendiente"] * 6)
    (root / DOMINANCE_EXACT).write_text(body, encoding="utf-8")
    (root / DOMINANCE_RIVAL).write_text(body + " factura", encoding="utf-8")
    return root / DOMINANCE_RIVAL


def _engine_for(tree: Path, database_path: Path) -> SearchEngine:
    database = SearchDatabase(database_path)
    Indexer(database).index_root(tree)
    return SearchEngine(database)


def _build_corpus(root: Path) -> Path:
    corpus_module.assert_labels_are_consistent()
    tree = root / "corpus"
    corpus_module.build(tree)
    return tree


def _count_boosted_exact(engine: SearchEngine, labelled_queries) -> int:
    """Exact-name matches that came back carrying a learning contribution."""
    boosted = 0
    for labelled in labelled_queries:
        for result in engine.search(
            labelled.query, limit=10, usage=True, explain=True,
            now=learning.MEASUREMENT_NOW,
        ):
            points = result.explain or {}
            if points.get("filename_exact", 0.0) > 0.0 and points.get("usage", 0.0) > 0.0:
                boosted += 1
    return boosted


def _margin_of(engine: SearchEngine, name: str, query: str) -> float:
    """How far ahead the named document leads, in final score."""
    results = engine.search(query, limit=10, usage=False,
                            now=learning.MEASUREMENT_NOW)
    scores = learning.scores(results)
    if name not in scores:
        return 0.0
    rivals = [value for key, value in scores.items() if key != name]
    return round(scores[name] - max(rivals, default=0.0), 6)


def _with_usage(database) -> SearchEngine:
    """A view over an existing database, so its events are already there."""
    return SearchEngine(database)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-044-"))
    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    extra: dict[str, object] = {}
    try:
        tree = _build_corpus(workspace)
        labelled_queries = corpus_module.LABELLED_QUERIES
        queries = [labelled.query for labelled in labelled_queries]

        def fresh(tag: str) -> SearchEngine:
            return _engine_for(tree, workspace / f"{tag}.db")

        # -- L1: cold start ------------------------------------------------
        cold = learning.cold_start(fresh("cold"), queries)
        measured["L1_cold_start_differences"] = len(cold["differences"])
        details["L1_cold_start_differences"] = (
            f"{cold['queries']} consultas, misma respuesta con y sin historial"
        )

        # -- L2: one accidental open --------------------------------------
        single = fresh("single")
        learning.record(single, [
            learning.UsageEvent(
                document=learning.document_id(single, WORKFLOW_PATH),
                query=WORKFLOW_QUERY,
            )
        ])
        lone = learning.cold_start(_with_usage(single.database), queries)
        measured["L2_single_open_differences"] = len(lone["differences"])
        details["L2_single_open_differences"] = (
            f"una sola apertura no cambia ninguna de las {lone['queries']} "
            "respuestas"
        )

        # -- L3: determinism ------------------------------------------------
        learned = fresh("determinism")
        target = learning.document_id(learned, WORKFLOW_PATH)
        learning.record(learned, learning.repeated_opens(
            target, WORKFLOW_QUERY, times=5))
        repeat = learning.determinism(learned, WORKFLOW_QUERY)
        measured["L3_nondeterministic_runs"] = 0 if repeat["stable"] else 1
        details["L3_nondeterministic_runs"] = (
            f"3 búsquedas con historial dan el mismo orden: {repeat['stable']}"
        )

        # -- L4: forgetting -------------------------------------------------
        history = learning.repeated_opens(
            target, WORKFLOW_QUERY, times=6, age_days=1.0
        )
        forgot = learning.forgetting(
            lambda: fresh("forget"), history, WORKFLOW_QUERY
        )
        measured["L4_histories_that_did_not_forget"] = int(
            not forgot["old_is_baseline"]
        )
        details["L4_histories_that_did_not_forget"] = (
            "6 aperturas de hoy cambian el orden; las mismas de hace 1200 "
            "días vuelven al orden base"
        )

        # -- L5: bounded ----------------------------------------------------
        shares = learning.max_share()
        headroom = learning.headroom_ratio()
        measured["L5_learning_stronger_than_recency"] = int(headroom > 1.0)
        extra["shares"] = shares
        extra["headroom"] = headroom
        details["L5_learning_stronger_than_recency"] = (
            f"aprendizaje {shares['usage'] * 100:.2f}% del puntaje frente a "
            f"recencia {shares['recency'] * 100:.2f}%; margen x{headroom}; "
            f"nombre exacto {shares['filename_exact'] * 100:.1f}% "
            f"(peso total {shares['total_weight']})"
        )

        # -- L6: an exact match is never boosted ----------------------------
        probe = fresh("exact")
        boosted_exact = _count_boosted_exact(probe, labelled_queries)
        measured["L6_exact_matches_boosted"] = boosted_exact
        details["L6_exact_matches_boosted"] = (
            f"{boosted_exact} coincidencias exactas recibieron impulso; el "
            f"efecto máximo del aprendizaje es {learning.usage_effect():.4f} "
            "de puntaje"
        )

        # -- L7: and learning cannot overtake one ----------------------------
        dominance_tree = workspace / "dominance"
        _write_dominance_pair(dominance_tree)
        dominator = _engine_for(dominance_tree, workspace / "dominance.db")
        rival_id = learning.document_id(dominator, DOMINANCE_RIVAL)
        learning.record(dominator, [
            learning.UsageEvent(document=rival_id, query=DOMINANCE_QUERY,
                                age_days=1.0)
            for _ in range(40)
        ])
        baseline_order = learning.order(dominator.search(
            DOMINANCE_QUERY, limit=5, usage=False, now=learning.MEASUREMENT_NOW))
        learned_order = learning.order(dominator.search(
            DOMINANCE_QUERY, limit=5, usage=True, now=learning.MEASUREMENT_NOW))
        # Only learning's doing counts: if the exact match does not lead at
        # baseline, that is the ranker's business, not this phase's.
        leads_at_baseline = baseline_order[:1] == [DOMINANCE_EXACT]
        overturns = leads_at_baseline and learned_order[:1] != [DOMINANCE_EXACT]
        measured["L7_exact_matches_overturned_by_learning"] = int(overturns)
        margin = _margin_of(dominator, DOMINANCE_EXACT, DOMINANCE_QUERY)
        effect = learning.usage_effect()
        extra["dominance"] = {
            "baseline_order": baseline_order,
            "learned_order": learned_order,
            "exact_margin": margin,
            "max_learning_effect": effect,
            "margin_over_effect": round(margin / effect, 2) if effect else None,
        }
        details["L7_exact_matches_overturned_by_learning"] = (
            f"tras 40 aperturas de «{DOMINANCE_RIVAL}», «{DOMINANCE_EXACT}» "
            f"sigue primero ({learned_order}); su margen ({margin:.4f}) es "
            f"{margin / effect:.1f}× el efecto máximo del aprendizaje"
        )

        # -- L8: an explicit filter still wins ------------------------------
        filter_engine = fresh("filtered")
        learning.record(filter_engine, learning.repeated_opens(
            learning.document_id(filter_engine, "calculo_matrices.txt"),
            WORKFLOW_QUERY, times=40,
        ))
        filtered = filter_engine.search(
            WORKFLOW_QUERY, limit=5, usage=True, doc_type="md",
            now=learning.MEASUREMENT_NOW,
        )
        respects = all(
            str(result.path).lower().endswith((".md", ".markdown"))
            for result in filtered
        )
        measured["L8_filters_overridden"] = 0 if respects else 1
        details["L8_filters_overridden"] = (
            f"con doc_type=md y 40 aperturas aprendidas de un .txt, "
            f"{len(filtered)} resultados y ninguno de otro tipo"
        )

        # -- L9: does it help a repeated workflow? ---------------------------
        sweep_engine = fresh("sweep")
        sweep = learning.promotion_sweep(sweep_engine, labelled_queries, times=6)
        improved_pairs = len(sweep["improved"])
        demoted_pairs = len(sweep["demoted"])
        measured["L9_workflows_learning_did_not_help"] = (
            0 if improved_pairs else 1
        )
        extra["sweep"] = {
            "considered": sweep["considered"],
            "already_first": sweep["already_first"],
            "improved": [list(item) for item in sweep["improved"][:8]],
            "demoted": [list(item) for item in sweep["demoted"][:8]],
        }
        details["L9_workflows_learning_did_not_help"] = (
            f"de {sweep['considered']} pares (consulta, documento), "
            f"{improved_pairs} mejoran y {demoted_pairs} bajan tras 6 "
            f"aperturas repetidas; {sweep['already_first']} ya estaban primeros"
        )

        # -- L10: no regression on the fixed corpus --------------------------
        # Two claims, because "MRR did not fall" and "nothing moved at all" are
        # different. The second is the stronger one and the one this phase can
        # actually make: with no events every signal is inert, so switching
        # learning on must be a no-op and not merely a harmless one.
        mrr_engine = _engine_for(tree, workspace / "mrr.db")
        plain_mrr = learning.mean_reciprocal_rank(
            mrr_engine, labelled_queries, usage=False)
        learned_mrr = learning.mean_reciprocal_rank(
            mrr_engine, labelled_queries, usage=True)
        moved = learning.queries_that_moved(mrr_engine, labelled_queries)
        regression = max(0.0, round(plain_mrr - learned_mrr, 6))
        measured["L10_mrr_regression"] = regression
        extra["mrr"] = {
            "baseline": round(plain_mrr, 6),
            "with_learning": round(learned_mrr, 6),
            "queries_moved": moved,
        }
        details["L10_mrr_regression"] = (
            f"MRR {plain_mrr:.4f} sin aprendizaje y {learned_mrr:.4f} con "
            f"aprendizaje activo y sin historial; {len(moved)} de "
            f"{len(labelled_queries)} consultas cambian de puntaje"
        )

        # -- L11: a material change is explainable ---------------------------
        explained = filter_engine.search(
            WORKFLOW_QUERY, limit=10, usage=True, explain=True,
            now=learning.MEASUREMENT_NOW,
        )
        boosted_rows = [
            result for result in explained
            if (result.explain or {}).get("usage", 0.0) > 0.0
        ]
        noted_rows = [
            result for result in boosted_rows
            if any("uso local" in note for note in (result.explain_notes or ()))
        ]
        measured["L11_boosts_without_an_explanation"] = (
            len(boosted_rows) - len(noted_rows)
        )
        details["L11_boosts_without_an_explanation"] = (
            f"{len(boosted_rows)} resultados con señal de aprendizaje, "
            f"{len(noted_rows)} con una frase que la explique"
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    verdicts = [
        Verdict(
            gate=GATE_LINES[gate],
            measured=float(measured[gate]),
            threshold=float(threshold),
            passed=measured[gate] <= threshold,
            detail=details[gate],
        )
        for gate, threshold in THRESHOLDS.items()
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 044 (aprendizaje local)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("peso de cada señal como parte del puntaje:")
    shares = extra.get("shares", {})
    for name in ("filename_exact", "phrase_exact", "usage", "recency"):
        if name in shares:
            print(f"  {name:<16} {shares[name] * 100:6.2f}%")
    print(f"  margen del aprendizaje frente a la recencia: x{extra.get('headroom')}")
    dominance = extra.get("dominance") or {}
    if dominance:
        print("-" * 100)
        print("predominio de la coincidencia exacta (L7):")
        print(f"  orden base    {dominance.get('baseline_order')}")
        print(f"  orden aprendido {dominance.get('learned_order')}")
        print(
            f"  margen {dominance.get('exact_margin')} frente a un efecto "
            f"máximo de {dominance.get('max_learning_effect')}: "
            f"x{dominance.get('margin_over_effect')}"
        )
    sweep = extra.get("sweep") or {}
    if sweep:
        print("-" * 100)
        print("barrido de flujo repetido (L9):")
        print(
            f"  {sweep.get('considered')} pares considerados, "
            f"{len(sweep.get('improved') or [])} mostrados"
        )
        for row in (sweep.get("improved") or []):
            print(f"    «{row[1]}» para «{row[0]}»: del puesto {row[2]} al {row[3]}")
    print("-" * 100)
    print("curva de olvido (un mismo historial, a distintas edades):")
    for age in (0, 30, 90, 365, 1200):
        print(
            f"  {age:>5} días  4 aperturas -> "
            f"{learn.usage_boost_from(4, age):.3f}   "
            f"40 aperturas -> {learn.usage_boost_from(40, age):.3f}"
        )

    payload = {
        "phase": "044",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "shares": extra.get("shares"),
        "headroom": extra.get("headroom"),
        "dominance": extra.get("dominance"),
        "sweep": extra.get("sweep"),
        "mrr": extra.get("mrr"),
        "decay": {
            str(age): {
                "four": round(learn.usage_boost_from(4, age), 6),
                "forty": round(learn.usage_boost_from(40, age), 6),
            }
            for age in (0, 30, 90, 365, 1200)
        },
    }
    out = ROOT / "evaluation" / "learning_baseline.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())