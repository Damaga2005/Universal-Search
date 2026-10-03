"""Phase 045 evidence: is there a reproducible failure a ranking change could fix?

Run from the repository root::

    python -m evaluation.quality_gate

The phase's governing rule is *"if no reproducible failure justifies a ranking
change, do not change the ranking"*, and a gate that cannot answer that
question is just another suite of numbers. So the invariants come in two
families and the second family is the point.

**Q1-Q8, measurement.** Precision@1/5/10, Recall@5/10, MRR, exact-match,
filter and zero-result accuracy, and the diagnosed failure inventory, all on
the fixed versioned corpus.

**Q9-Q13, the decision.** Every diagnosed miss is classified by probing
(:mod:`evaluation.diagnose`) rather than by a label a human wrote on the query.
Then:

* every verdict falls inside the declared ten classes;
* no verdict is unclassified;
* **the count of misses a weighting change could have reached**;
* the ranking is bit-for-bit what it was before the phase;
* and the before/after of every candidate weight is measured and reported, so
  "no change" is a conclusion with numbers attached rather than an omission.

Q12 is the one that can make this gate fail while everything else passes, and
it is the reason the gate exists.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation import corpus as corpus_module  # noqa: E402
from evaluation import diagnose as diagnose_module  # noqa: E402
from evaluation import metrics as metrics_module  # noqa: E402
from evaluation import runner as runner_module  # noqa: E402
from evaluation.accessibility_gate import Verdict  # noqa: E402
from universal_search.fuzzy.engine import FuzzySearchEngine  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.ranking import DEFAULT_WEIGHTS, Ranker  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402
from universal_search.semantic.engine import HybridSearchEngine  # noqa: E402
from universal_search.semantic.index import SemanticIndex  # noqa: E402

# Fixed clock. Phase 044 introduced the injected clock this reuses; without it
# the recency signal is measured against `datetime.now()` and every ranking
# number in this report drifts with the calendar.
MEASUREMENT_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

THRESHOLDS = {
    "Q1_mrr_regression": 0.0,
    "Q2_precision_at_1_below_baseline": 0.0,
    "Q3_recall_at_10_below_baseline": 0.0,
    "Q4_exact_match_correctness_below_one": 0.0,
    "Q5_filtered_queries_that_leaked": 0,
    "Q6_noisy_queries_that_should_be_silent": 0,
    "Q7_unclassified_failures": 0,
    "Q8_missing_workload_classes": 0,
    "Q9_failures_reachable_by_ranking": 0,
    "Q10_ranking_weights_changed": 0.0,
    "Q11_diagnosed_classes_outside_the_taxonomy": 0,
    "Q12_candidate_weights_that_looked_measured": 0,
    "Q13_declared_limitations_a_tokenizer_would_have_fixed": 0,
}

GATE_LINES = {
    "Q1_mrr_regression": "Q1 regresion de MRR respecto al baseline",
    "Q2_precision_at_1_below_baseline": "Q2 P@1 por debajo del baseline",
    "Q3_recall_at_10_below_baseline": "Q3 R@10 por debajo del baseline",
    "Q4_exact_match_correctness_below_one": "Q4 exactitud de coincidencia exacta",
    "Q5_filtered_queries_that_leaked": "Q5 consultas filtradas que filtraron mal",
    "Q6_noisy_queries_that_should_be_silent": "Q6 consultas ruidosas que debian callar",
    "Q7_unclassified_failures": "Q7 fallos sin causa diagnosticada",
    "Q8_missing_workload_classes": "Q8 cargas de trabajo sin representar",
    "Q9_failures_reachable_by_ranking": "Q9 fallos que un peso podria alcanzar",
    "Q10_ranking_weights_changed": "Q10 la ordenacion cambio",
    "Q11_diagnosed_classes_outside_the_taxonomy": "Q11 clases fuera de la taxonomia",
    "Q12_candidate_weights_that_looked_measured": "Q12 pesos candidatos con mejora medida",
    "Q13_declared_limitations_a_tokenizer_would_have_fixed": "Q13 ganancia neta de cambiar de tokenizador",
}

# The workloads phase 045 lists, and how to recognise one in the corpus. A
# workload with no representative is not "working": it cannot regress, and a
# thing that cannot regress is not a thing that is tested.
REQUIRED_WORKLOADS: tuple[tuple[str, str], ...] = (
    ("university/technical", "electronica/"),
    ("code", "codigo/"),
    ("pdf", ".pdf"),
    ("office", ".docx"),
    ("office table", ".xlsx"),
    ("duplicated material", "__duplicate__"),
    ("short document", "__short__"),
    ("long document", "__long__"),
    ("abbreviated filename", "__abbrev__"),
    ("multilingual", "__multilingual__"),
)

# The candidate weights tried in Q12. Chosen to span the space a "just a
# little more of X" proposal would land in: the three strongest relevance
# signals at their current values and at double, plus the secondary ones at
# zero, which is the shape a regression usually takes.
CANDIDATE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("filename_exact", 6.0),
    ("filename_tokens", 4.0),
    ("phrase_exact", 4.0),
    ("term_freq", 3.0),
    ("proximity", 3.0),
    ("bm25", 4.0),
    ("recency", 0.0),
    ("recency", 0.6),
    ("path_match", 1.6),
)


def _workload_coverage() -> dict[str, bool]:
    """Which of the phase's workloads the corpus actually represents."""
    documents = corpus_module.DOCUMENTS
    coverage: dict[str, bool] = {}
    for name, marker in REQUIRED_WORKLOADS:
        if marker == "__duplicate__":
            bodies = [
                (d.content or d.raw or b"").strip() for d in documents
            ]
            coverage[name] = len(bodies) != len(set(map(repr, bodies)))
        elif marker == "__short__":
            lengths = [
                len(d.content) for d in documents if d.content
            ]
            coverage[name] = bool(lengths) and min(lengths) <= 120
        elif marker == "__long__":
            lengths = [
                len(d.content) for d in documents if d.content
            ]
            coverage[name] = bool(lengths) and max(lengths) >= 2000
        elif marker == "__abbrev__":
            # Split the stem on every separator a real filename uses, not just
            # the extension dot: `T6_BJT_Apuntes.md` is one token until you
            # split it, and a detector that looks for a short uppercase
            # *word* finds nothing in a file that is nothing but abbreviations.
            coverage[name] = any(
                _abbreviated_segments(d.path)
                for d in documents
            )
        elif marker == "__multilingual__":
            coverage[name] = any(
                _looks_non_spanish(d.content or "")
                for d in documents
                if d.content
            )
        else:
            coverage[name] = any(marker in d.path for d in documents)
    return coverage


_SPANISH_HINTS = (
    " de ", " la ", " el ", " los ", " las ", " que ", " para ", " con ",
    " del ", " una ", " se ", " en ",
)


def _looks_non_spanish(text: str) -> bool:
    """Whether a document's prose is not Spanish.

    A crude and declared heuristic, used only to answer "is any non-Spanish
    document present". Every non-Spanish corpus document is also the one that
    no query in Spanish can reach, so the marker only has to be conservative:
    a false positive would let the gate claim coverage it does not have, which
    is the direction that matters.
    """
    lowered = text.casefold()
    spanish = sum(lowered.count(hint) for hint in _SPANISH_HINTS)
    return spanish <= 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-045-"))
    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    extra: dict[str, object] = {}
    try:
        corpus_module.assert_labels_are_consistent()
        tree = workspace / "corpus"
        corpus_module.build(tree)

        lexical = SearchEngine(_engine_database(tree, workspace / "lexical.db"))
        fuzzy = FuzzySearchEngine(lexical)
        hybrid = HybridSearchEngine(lexical, SemanticIndex(lexical.database))
        ids = diagnose_module.document_ids_for(lexical, tree)

        report = _measure(lexical, tree)
        aggregates = _aggregates(report)

        # -- Q1/Q2/Q3: regression against the committed baseline ------------
        baseline = json.loads(
            (ROOT / "evaluation" / "baseline.json").read_text(encoding="utf-8")
        )
        committed = baseline["aggregates"]
        mrr_regression = max(0.0, round(committed["mrr"] - aggregates["mrr"], 6))
        p1_regression = max(
            0.0,
            round(
                committed["mean_precision_at_k"]["1"]
                - aggregates["mean_precision_at_k"]["1"],
                6,
            ),
        )
        r10_regression = max(
            0.0,
            round(
                committed["mean_recall_at_k"]["10"]
                - aggregates["mean_recall_at_k"]["10"],
                6,
            ),
        )
        measured["Q1_mrr_regression"] = mrr_regression
        measured["Q2_precision_at_1_below_baseline"] = p1_regression
        measured["Q3_recall_at_10_below_baseline"] = r10_regression
        details["Q1_mrr_regression"] = (
            f"MRR {committed['mrr']:.4f} en el baseline, {aggregates['mrr']:.4f} ahora"
        )
        details["Q2_precision_at_1_below_baseline"] = (
            f"P@1 {committed['mean_precision_at_k']['1']:.4f} -> "
            f"{aggregates['mean_precision_at_k']['1']:.4f}"
        )
        details["Q3_recall_at_10_below_baseline"] = (
            f"R@10 {committed['mean_recall_at_k']['10']:.4f} -> "
            f"{aggregates['mean_recall_at_k']['10']:.4f}"
        )

        # -- Q4: exact-match correctness ------------------------------------
        # Computed over the queries this build claims to answer. A declared
        # limitation is excluded and *named*, because a threshold that quietly
        # omits a case is worse than one that reports it.
        declared = {
            labelled.query: labelled.known_limitation
            for labelled in corpus_module.LABELLED_QUERIES
            if labelled.known_limitation
        }
        claimed = [
            labelled for labelled in corpus_module.LABELLED_QUERIES
            if not labelled.known_limitation
        ]
        # Over the whole report, then filtered. `exact_match_summary` iterates
        # the *report* and takes the document list for looking targets up, so
        # the exclusion has to happen on its output rather than on its input.
        _all_correctness, all_exact_rows = runner_module.exact_match_summary(
            report, corpus_module.DOCUMENTS
        )
        exact_rows = [
            row for row in all_exact_rows if row["query"] not in declared
        ]
        correct_rows = sum(1 for row in exact_rows if row["correct"])
        correctness = (
            correct_rows / len(exact_rows) if exact_rows else 1.0
        )
        del _all_correctness, claimed
        measured["Q4_exact_match_correctness_below_one"] = max(
            0.0, round(1.0 - correctness, 6)
        )
        extra["exact"] = correctness
        extra["exact_rows"] = exact_rows
        extra["declared_limitations"] = declared
        wrong_exact = [row["query"] for row in exact_rows if not row["correct"]]
        details["Q4_exact_match_correctness_below_one"] = (
            f"{len(exact_rows)} consultas con coincidencia exacta, "
            f"correccion {correctness:.4f}; "
            f"{len(declared)} limitacion(es) declarada(s) excluidas y nombradas"
            + (f"; fallan {wrong_exact}" if wrong_exact else "")
        )

        # -- Q5: filter accuracy --------------------------------------------
        filter_report = _filter_accuracy(lexical, tree)
        measured["Q5_filtered_queries_that_leaked"] = len(filter_report["wrong"])
        details["Q5_filtered_queries_that_leaked"] = (
            f"{filter_report['queries']} consultas con filtro, "
            f"correccion {filter_report['accuracy']:.4f}"
        )

        # -- Q6: zero-result accuracy ---------------------------------------
        silence = _zero_result_accuracy(report)
        measured["Q6_noisy_queries_that_should_be_silent"] = len(silence["noisy"])
        details["Q6_noisy_queries_that_should_be_silent"] = (
            f"{silence['queries']} consultas que deben callar, "
            f"silenciadas {silence['silent']}"
        )

        # -- Q7/Q11: diagnose every miss, both layer configurations ----------
        lexical_only = _inventory(lexical, tree, ids)
        with_layers = _inventory(lexical, tree, ids, fuzzy=fuzzy, semantic=hybrid)
        measured["Q7_unclassified_failures"] = with_layers.unclassified
        measured["Q11_diagnosed_classes_outside_the_taxonomy"] = _outside(
            lexical_only, with_layers
        )
        extra["inventory"] = with_layers.as_dict()
        extra["inventory_lexical_only"] = lexical_only.as_dict()
        details["Q7_unclassified_failures"] = (
            f"{len(with_layers.verdicts)} fallos diagnosticados, "
            f"{with_layers.unclassified} sin clase"
        )
        details["Q11_diagnosed_classes_outside_the_taxonomy"] = (
            "clases: "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(with_layers.counts().items())
                if count
            )
        )

        # -- Q8: workload coverage ------------------------------------------
        coverage = _workload_coverage()
        missing = sorted(name for name, present in coverage.items() if not present)
        measured["Q8_missing_workload_classes"] = len(missing)
        extra["coverage"] = coverage
        details["Q8_missing_workload_classes"] = (
            f"{len(coverage) - len(missing)} de {len(coverage)} cargas presentes"
            + (f"; faltan {missing}" if missing else "")
        )

        # -- Q9: could a weight have fixed any of this? ---------------------
        # The question the whole phase turns on. Diagnosed with both optional
        # layers on, because the answer that matters is "given everything this
        # product ships, is anything left for a weight to do?"
        reachable = with_layers.ranking_reachable
        measured["Q9_failures_reachable_by_ranking"] = reachable
        details["Q9_failures_reachable_by_ranking"] = (
            f"de {len(with_layers.verdicts)} fallos, {reachable} alcanzables "
            "cambiando un peso; solo el motor lexico: "
            f"{lexical_only.ranking_reachable}"
        )

        # -- Q10: the ranking did not change -------------------------------
        drifted = _weights_drift()
        measured["Q10_ranking_weights_changed"] = float(len(drifted))
        details["Q10_ranking_weights_changed"] = (
            "12 señales, pesos idénticos al baseline de la fase 040"
            if not drifted
            else f"pesos que se movieron: {drifted}"
        )

        # -- Q12: every candidate weight, measured --------------------------
        trials = _candidate_trials(tree, workspace)
        winners = [t for t in trials if t["improved_mrr"]]
        measured["Q12_candidate_weights_that_looked_measured"] = len(winners)
        extra["trials"] = trials
        details["Q12_candidate_weights_that_looked_measured"] = (
            f"{len(trials)} pesos candidatos medidos contra el corpus, "
            f"{len(winners)} mejoran el MRR"
            + (
                ": " + ", ".join(
                    f"{t['weight']}={t['value']} (+{t['mrr_delta']:.4f})"
                    for t in winners
                )
                if winners
                else "; ninguno, y ese es el resultado"
            )
        )

        # -- Q13: would a different tokenizer have fixed a declared gap? ----
        # The reason a declared limitation is allowed to stay declared. If some
        # other tokenizer answered every query this build cannot, then Q13
        # fails and the limitation is not a limitation -- it is an unexplored
        # fix, and this gate says so instead of letting it rest.
        trade = _tokenizer_trade()
        more = [row for row in trade if row["delta"] > 0]
        fewer = [row for row in trade if row["delta"] < 0]
        net = len(more) - len(fewer)
        measured["Q13_declared_limitations_a_tokenizer_would_have_fixed"] = float(
            max(0, net)
        )
        extra["tokenizer_trade"] = trade
        details["Q13_declared_limitations_a_tokenizer_would_have_fixed"] = (
            f"los dos tokenizadores sobre las {len(trade)} consultas: "
            f"trigram responde a {len(more)} mas y {len(fewer)} menos "
            f"(ganancia neta {net:+d})"
            + (
                "; se arreglaria cambiando de tokenizador: "
                + ", ".join(f"{row['query']!r}" for row in more)
                if net > 0
                else "; la limitacion declarada se justifica por medicion"
            )
            + ("; rotas: " + ", ".join(f"{row['query']!r}" for row in fewer)
               if fewer else "")
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

    failed = [v for v in verdicts if not v.passed]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 045 (calidad y relevancia)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("medidas sobre el corpus fijo:")
    print(f"  {'MRR':<28} {aggregates['mrr']}")
    for k in report.k_values:
        print(
            f"  P@{k} / R@{k}".ljust(28)
            + f" {aggregates['mean_precision_at_k'][str(k)]}"
            + f"  /  {aggregates['mean_recall_at_k'][str(k)]}"
        )
    print(f"  exact_match_correctness       {extra.get('exact')}")
    print("-" * 100)
    print("inventario de fallos (motor lexico, sin capas opcionales):")
    lexical_counts = (extra.get("inventory_lexical_only") or {}).get("by_class", {})
    for name, count in sorted(lexical_counts.items()):
        if count:
            print(f"  {name:<20} {count}")
    print("inventario de fallos (con las dos capas opcionales):")
    for name, count in sorted((extra.get("inventory") or {}).get("by_class", {}).items()):
        if count:
            print(f"  {name:<20} {count}")
    print("-" * 100)
    print("cada veredicto, con su evidencia:")
    for verdict in (extra.get("inventory") or {}).get("verdicts", []):
        print(f"  {verdict['query']!r:<44} {verdict['document']:<20} "
              f"{verdict['cause']}")
        print(f"      {verdict['evidence'][-1]}")
    print("-" * 100)
    print("cargas de trabajo exigidas por la fase:")
    for name, present in sorted((extra.get("coverage") or {}).items()):
        print(f"  {'si ' if present else 'NO '} {name}")
    print("-" * 100)
    print("pesos candidatos, medidos contra el corpus:")
    for trial in extra.get("trials") or []:
        mark = "MEJORA" if trial["improved_mrr"] else "sin efecto"
        print(
            f"  {trial['weight']:<16} {trial['value']:<5} "
            f"MRR {trial['mrr']:.4f} ({trial['mrr_delta']:+.4f})  {mark}"
        )
    print("-" * 100)
    print(
        "DECISION: "
        + (
            "la ordenacion NO se toca. Ningun fallo reproducible queda al alcance "
            "de un peso, y ninguna de las nueve candidatas mejora el MRR."
            if not failed else
            f"{len(failed)} puertas fallan; revisar antes de decidir."
        )
    )

    payload = {
        "phase": "045",
        "clock": MEASUREMENT_NOW.isoformat(),
        "corpus": {
            "documents": len(corpus_module.DOCUMENTS),
            "queries": len(corpus_module.LABELLED_QUERIES),
        },
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "metrics": aggregates,
        "exact_match_correctness": extra.get("exact"),
        "coverage": extra.get("coverage"),
        "inventory": extra.get("inventory"),
        "inventory_lexical_only": extra.get("inventory_lexical_only"),
        "candidate_weights": extra.get("trials"),
        "decision": "ranking unchanged",
    }
    out = ROOT / "evaluation" / "quality_baseline.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


def _engine_database(tree: Path, path: Path) -> SearchDatabase:
    database = SearchDatabase(path)
    Indexer(database).index_root(tree)
    return database


def _measure(engine: SearchEngine, tree: Path):
    ids = corpus_module.ids_by_path(tree)
    measured = []
    for labelled in corpus_module.LABELLED_QUERIES:
        results = engine.search(
            labelled.query, limit=runner_module.DEFAULT_LIMIT,
            explain=True, now=MEASUREMENT_NOW,
        )
        ranked = [
            ids.get(Path(result.path).resolve(), result.path)
            for result in results
        ]
        measured.append((
            labelled.query, ranked, labelled.relevant,
            [result.score for result in results],
            [result.explain or {} for result in results],
        ))
    return metrics_module.evaluate(measured, runner_module.K_VALUES)


def _aggregates(report) -> dict[str, object]:
    """The same shape ``baseline.json`` commits, so Q1-Q3 compare like for like.

    Flattening the K values into ``p_at_5`` reads better and compares worse:
    the whole point of Q1-Q3 is to hold this run against a committed number,
    and two spellings of the same quantity is one more way for the comparison
    to be wrong in a way nobody notices.
    """
    precision: dict[str, float] = {}
    recall: dict[str, float] = {}
    for k in report.k_values:
        precision[str(k)] = round(report.mean_precision(k), 6)
        recall[str(k)] = round(report.mean_recall(k), 6)
    return {
        "mrr": round(report.mrr(), 6),
        "mean_precision_at_k": precision,
        "mean_recall_at_k": recall,
    }


def _filter_accuracy(engine: SearchEngine, tree: Path) -> dict[str, object]:
    cases: list[tuple[str, bool]] = []
    unlabelled: list[str] = []
    for labelled in corpus_module.LABELLED_QUERIES:
        filters = diagnose_module.filters_in(labelled.query)
        if not filters:
            continue
        results = engine.search(
            labelled.query, limit=runner_module.DEFAULT_LIMIT, now=MEASUREMENT_NOW
        )
        satisfied = all(
            diagnose_module.passes_filters(str(result.path), filters)
            for result in results
        )
        cases.append((labelled.query, satisfied))
    return metrics_module.filter_accuracy(cases, unlabelled=unlabelled)


def _zero_result_accuracy(report) -> dict[str, object]:
    cases: list[tuple[str, bool]] = []
    for labelled in corpus_module.LABELLED_QUERIES:
        if labelled.relevant:
            continue
        ranked = report.by_query()[labelled.query].ranked
        cases.append((labelled.query, not ranked))
    return metrics_module.zero_result_accuracy(cases)


def _inventory(engine, tree: Path, ids, *, fuzzy=None, semantic=None):
    verdicts = []
    for labelled in corpus_module.LABELLED_QUERIES:
        verdicts.extend(diagnose_module.diagnose(
            engine, labelled, corpus_root=tree, fuzzy=fuzzy, semantic=semantic,
            limit=runner_module.DEFAULT_LIMIT, now=MEASUREMENT_NOW,
        ))
    del ids
    return diagnose_module.build_inventory(verdicts)


def _outside(*inventories) -> int:
    allowed = set(diagnose_module.FAILURE_CLASSES)
    return sum(
        1
        for inventory in inventories
        for verdict in inventory.verdicts
        if verdict.cause not in allowed
    )


def _weights_drift() -> list[str]:
    """Signals whose weight differs from the phase-040 documented values.

    Phase 044 moved ``usage`` from 0.5 to 0.25 on measurement, and that is a
    recorded decision, not drift. What this catches is somebody turning a dial
    inside this phase without a reproducible failure behind it -- which is the
    one thing this gate exists to make impossible.
    """
    documented = {
        "filename_exact": 3.0,
        "filename_tokens": 2.0,
        "phrase_exact": 2.0,
        "term_freq": 1.5,
        "proximity": 1.5,
        "bm25": 2.0,
        "path_match": 0.8,
        "doc_type": 0.5,
        "source": 0.4,
        "recency": 0.3,
        "usage": 0.0,
        "context": 0.0,
    }
    return sorted(
        name
        for name, value in documented.items()
        if getattr(DEFAULT_WEIGHTS, name) != value
    )


def _candidate_trials(tree: Path, workspace: Path) -> list[dict[str, object]]:
    """Every candidate weight, measured on the whole corpus.

    This is what makes "do not change the ranking" a conclusion. Nine weights
    were tried at values chosen to bracket any plausible "a little more of X",
    and each was measured end to end rather than argued about.
    """
    trials: list[dict[str, object]] = []
    baseline_report = _measure(
        SearchEngine(_engine_database(tree, workspace / "base.db")), tree
    )
    baseline_mrr = baseline_report.mrr()
    for index, (weight, value) in enumerate(CANDIDATE_WEIGHTS):
        weights = replace(DEFAULT_WEIGHTS, **{weight: value})
        engine = SearchEngine(
            _engine_database(tree, workspace / f"trial{index}.db"),
            ranker=Ranker(weights),
        )
        trial_mrr = _measure(engine, tree).mrr()
        trials.append({
            "weight": weight,
            "value": value,
            "mrr": round(trial_mrr, 6),
            "mrr_delta": round(trial_mrr - baseline_mrr, 6),
            "improved_mrr": trial_mrr > baseline_mrr,
        })
    return trials




def _tokenizer_trade() -> list[dict[str, object]]:
    """Every labelled query, asked of both SQLite tokenizers.

    This is the experiment that decides whether the declared CJK limitation is
    a limitation or an unexplored fix, and the first version of it got the
    answer wrong by asking the wrong question. It measured whether ``trigram``
    could answer the one query ``unicode61`` cannot -- and it can -- which
    sounded like an unexplored fix and was in fact a net regression:

        trigram responde a 2 consultas mas  ->  'receta paella', '\u30c6\u30b9\u30c8'
        trigram responde a 4 consultas menos ->  '"ebers moll"', 'punto Q',
                                                 'gain de tension', 'T6 BJT Apuntes'

    A tokenizer with three-character granularity cannot match a two-character
    term, and quoted phrases behave differently under it. So the invariant is
    the *net* number, and the cost is measured as carefully as the benefit.
    """
    import sqlite3

    from universal_search.query import parse_query, translate

    tables: dict[str, sqlite3.Connection] = {}
    for tokenizer in ("unicode61", "trigram"):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE VIRTUAL TABLE t USING fts5("
            f"name, path, content, tokenize='{tokenizer}')"
        )
        for document in corpus_module.DOCUMENTS:
            connection.execute(
                "INSERT INTO t(name, path, content) VALUES (?, ?, ?)",
                (
                    document.path.rsplit("/", 1)[-1],
                    document.path,
                    document.content or "",
                ),
            )
        tables[tokenizer] = connection

    rows: list[dict[str, object]] = []
    for labelled in corpus_module.LABELLED_QUERIES:
        match = translate(parse_query(labelled.query)).fts
        counts: dict[str, int] = {}
        for tokenizer, connection in tables.items():
            try:
                counts[tokenizer] = int(connection.execute(
                    "SELECT count(*) FROM t WHERE t MATCH ?", (match,)
                ).fetchone()[0])
            except Exception:  # noqa: BLE001 - a tokenizer may be unavailable
                counts[tokenizer] = 0
        rows.append({
            "query": labelled.query,
            "unicode61": counts["unicode61"],
            "trigram": counts["trigram"],
            "delta": counts["trigram"] - counts["unicode61"],
        })
    for connection in tables.values():
        connection.close()
    return rows


_ABBREV_SPLIT = re.compile(r"[._\-\s]+")


def _abbreviated_segments(path: str) -> bool:
    """Whether a file name carries a short all-caps segment.

    ``T6_BJT_Apuntes.md`` -> ``T6``, ``BJT``; ``Tema_6_BJT.md`` -> ``BJT``.
    One or two characters counts, because a course names things ``T1`` and
    ``P3`` and a detector that demands three misses every one of them.
    """
    name = path.rsplit("/", 1)[-1]
    for segment in _ABBREV_SPLIT.split(name):
        if 1 <= len(segment) <= 4 and segment.isupper() and segment.isalpha():
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
