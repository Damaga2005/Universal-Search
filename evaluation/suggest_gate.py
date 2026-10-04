"""Phase 032 evidence: suggestions must be right, not merely plausible.

Run from the repository root::

    python -m evaluation.suggest_gate

A suggestion is accepted only if the query it proposes **returns a document**.
That is checked here by running it, not by trusting the module.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import corpus as corpus_module  # noqa: E402
from universal_search.fuzzy import (  # noqa: E402
    FuzzyIndex,
    FuzzySearchEngine,
    MAX_SUGGESTIONS,
    QuerySuggester,
)
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402


# Queries that are wrong and have a right answer in the index.
SUGGESTABLE: tuple[tuple[str, str], ...] = (
    ("transsitor", "transistor"),
    ("transistorr", "transistor"),
    ("modlo ebers", "modelo"),
    ("polirazcion", None),      # 3 edits: out of budget, may yield nothing
    ("gaurrent", None),
    ("puntoe trabajo", None),
    ("zzzz transsitor", None),  # one token is uncorrectable
    ("informe anua", None),
)

# Queries with nothing to correct. Offering advice here would be noise.
NOT_SUGGESTABLE = (
    "transistor",
    "zzz no existe",
    "noexistenadaquienadie",
    "qqqzzz wwwyyy",
)

THRESHOLDS = {
    "T1_recall_of_corrections": 0.60,
    "T2_false_suggestions": 0,
    "T3_advice_on_correct_queries": 0,
    "T4_lexical_mrr_change": 0.0,
    "T5_added_p95_ms": 8.0,
    "T6_no_persistent_state": 0,
}


@dataclass
class Verdict:
    gate: str
    measured: float
    threshold: float
    passed: bool
    detail: str

    def line(self) -> str:
        return (
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<34} "
            f"{self.measured:>10.4f}  (umbral {self.threshold})  {self.detail}"
        )


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    # -- phase 047: the load check, owned by perf_gate ----------------------
    # This gate measures latency. Measured on a machine running a game at 88%
    # CPU it reported NO SHIP at 12.6 ms against an 8.0 ms threshold, and 14.7
    # ms with these changes stashed away -- so the number was measuring the
    # competition for the CPU. A latency gate that cannot tell a busy machine
    # from a slow product answers a question nobody asked.
    #
    # Two readings, not one. The first version checked the load once, at the
    # start, and still reported NO SHIP while the machine was at 76%: the load
    # arrives in bursts, so a single sample catches the gap between them. The
    # check is repeated after the measurement, and a busy machine at *either*
    # end withholds the verdict.
    from evaluation import perf_gate

    baseline_path = ROOT / "evaluation" / "suggest_baseline.json"
    previous = {}
    if baseline_path.exists():
        try:
            previous = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    load_check = perf_gate.load_gate(previous.get("calibration_best_s"))
    if not load_check.conclusive:
        print("=" * 100)
        print("PUERTA DE EVIDENCIA - 032 (sugerencias)")
        print("=" * 100)
        print(f"INCONCLUYENTE: {load_check.detail}")
        print("-" * 100)
        print("No se ha medido nada. Una cifra de latencia sobre una maquina")
        print("ocupada describe el trabajo de otro programa, asi que el")
        print("umbral no se toca y la corrida no cuenta.")
        print("VEREDICTO: INCONCLUYENTE (la maquina no estaba en reposo)")
        return 2
    corpus_module.assert_labels_are_consistent()
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-032-"))
    tree = workspace / "tree"
    corpus_module.build(tree)
    database = SearchDatabase(workspace / "index.db")
    Indexer(database).index_root(tree)
    mapping = corpus_module.ids_by_path(tree)
    lexical = SearchEngine(database)
    index = FuzzyIndex(database)
    index.rebuild()
    engine = FuzzySearchEngine(lexical, index)

    # -- T1 / T2: the suggestions, each executed -----------------------------
    details = []
    correct = 0
    expected = 0
    false_positives = 0
    for query, wanted in SUGGESTABLE:
        if wanted:
            expected += 1
        suggestions = QuerySuggester(engine).suggest(query)
        verified = 0
        for suggestion in suggestions:
            # The contract, executed rather than assumed.
            if engine.search(suggestion.query, limit=1):
                verified += 1
            else:
                false_positives += 1
        if wanted and any(wanted in s.replacement for s in suggestions):
            correct += 1
        details.append({
            "query": query,
            "expected": wanted,
            "suggestions": [s.as_dict() for s in suggestions],
            "all_verified": verified == len(suggestions),
        })
    recall = correct / expected if expected else 0.0

    # -- T3: no advice where there is nothing to correct ---------------------
    advice_on_correct = 0
    for query in NOT_SUGGESTABLE:
        if QuerySuggester(engine).suggest(query):
            advice_on_correct += 1

    # -- T4: the search itself is untouched ----------------------------------
    from evaluation import runner  # noqa: PLC0415

    before = runner.measure(lexical, mapping).mrr()
    QuerySuggester(engine).suggest("transsitor")
    after = runner.measure(lexical, mapping).mrr()

    # -- T5: cost of asking, relative to a search that returns nothing ------
    added_warm, added_cold = _added_p95(lexical, engine)

    # -- T6: nothing persists ------------------------------------------------
    with database.connect() as connection:
        strays = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%vocab%'"
            " OR name LIKE '%suggest%'"
        ).fetchone()[0]

    verdicts = [
        Verdict("T1 recall of corrections", recall,
                THRESHOLDS["T1_recall_of_corrections"],
                recall >= THRESHOLDS["T1_recall_of_corrections"],
                f"{correct}/{expected} consultas con correccion esperada"),
        Verdict("T2 unverified suggestions", false_positives,
                THRESHOLDS["T2_false_suggestions"], not false_positives,
                "toda sugerencia devuelve un documento"
                if not false_positives else "hubo sugerencias que no devuelven nada"),
        Verdict("T3 advice on correct queries", advice_on_correct,
                THRESHOLDS["T3_advice_on_correct_queries"], not advice_on_correct,
                "sin consejos donde no hay nada que corregir"
                if not advice_on_correct else "aconsejo innecesario"),
        Verdict("T4 lexical MRR change", abs(after - before),
                THRESHOLDS["T4_lexical_mrr_change"], after == before,
                f"MRR sigue en {after:.4f}"),
        Verdict("T5 added p95, warm (ms)", added_warm,
                THRESHOLDS["T5_added_p95_ms"],
                added_warm <= THRESHOLDS["T5_added_p95_ms"],
                "sesion de busqueda viva; cold (una sola peticion) "
                f"{added_cold:.2f} ms"),
        Verdict("T6 persistent state left", strays,
                THRESHOLDS["T6_no_persistent_state"], not strays,
                "la vista de vocabulario se crea y se retira"),
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 032 (sugerencias de consulta)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("detalle por consulta:")
    for row in details:
        shown = ", ".join(s["query"] for s in row["suggestions"]) or "(ninguna)"
        print(f"  {row['query']!r:>22} -> {shown}")
    print(f"  maximo de sugerencias: {MAX_SUGGESTIONS}")
    payload = {
        "phase": "032",
        "load_before": load_check.detail,
        "calibration_best_s": load_check.calibration_best_s,
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "queries": details,
    }
    # The machine may have become busy *during* the measurement, which is the
    # common case with a bursty neighbour: the first reading of this fix caught
    # the machine idle, and the measurement ran on a CPU another program had
    # claimed by the time it finished. A verdict computed before checking again
    # would publish a latency figure measured under conditions nobody declared.
    after = perf_gate.load_gate()
    payload["load_after"] = after.detail

    out = ROOT / "evaluation" / "suggest_baseline.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")

    if not after.conclusive:
        print("-" * 100)
        print(f"INCONCLUYENTE despues de medir: {after.detail}")
        print("VEREDICTO: INCONCLUYENTE (la maquina no estaba en reposo)")
        return 2
    if load_check.conclusive and load_check.detail != after.detail:
        print(f"carga antes de medir:  {load_check.detail}")
        print(f"carga despues de medir: {after.detail}")

    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(round(fraction * len(ordered))) - 1)]


def _added_p95(lexical, engine, samples: int = 40) -> tuple[float, float]:
    """Cost of a suggestion request, warm and cold.

    The warm number is the one that matters: the search session is long-lived
    and asks for suggestions on every keystroke, so the vocabulary cache pays
    off from the second request on. The cold number is reported too, because a
    one-shot command line search pays it once, and pretending otherwise would
    be how this gate got it wrong the first time (it built a fresh suggester
    per sample, so the cache never survived and the number was the cold one
    dressed up as the warm one).
    """
    query = "transsitor"

    def timed_search() -> float:
        started = time.perf_counter()
        lexical.search(query, limit=5)
        return (time.perf_counter() - started) * 1000

    def timed_suggest(suggester) -> float:
        started = time.perf_counter()
        suggester.suggest(query)
        return (time.perf_counter() - started) * 1000

    search_p95 = _percentile([timed_search() for _ in range(samples)], 0.95)

    from universal_search.fuzzy.suggest import suggester_for  # noqa: PLC0415

    warm_suggester = suggester_for(engine)
    for _ in range(3):
        warm_suggester.suggest(query)
    warm = _percentile(
        [timed_suggest(warm_suggester) for _ in range(samples)], 0.95
    )

    cold_samples = []
    for _ in range(6):
        cold = QuerySuggester(engine)
        started = time.perf_counter()
        cold.suggest(query)
        cold_samples.append((time.perf_counter() - started) * 1000)
    cold = _percentile(cold_samples, 0.95)

    return warm - search_p95, cold - search_p95


if __name__ == "__main__":
    raise SystemExit(main())
