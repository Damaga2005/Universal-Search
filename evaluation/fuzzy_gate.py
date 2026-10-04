"""Phase 031 evidence: the six gates of the design, measured.

Run from the repository root::

    python -m evaluation.fuzzy_gate

Nothing here is a claim; every number is printed next to its threshold. The
phase ships only if T1 and T2 pass, and the others must not regress.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import corpus as corpus_module  # noqa: E402
from evaluation import runner  # noqa: E402
from universal_search.fuzzy import (  # noqa: E402
    FuzzyIndex,
    FuzzySearchEngine,
    MAX_TRIGRAMS_PER_DOC,
)
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402
from universal_search.semantic import SemanticIndex  # noqa: E402


# The failure classes phase 031 exists to fix, as (query, expected document
# id fragment). Every one of these retrieves nothing on the lexical engine
# today. Cases that the *design* cannot serve are deliberately absent and
# listed under OUT_OF_SCOPE below, rather than quietly dropped.
# (misspelled query, corpus ids that are a correct answer for it).
#
# Phase 045 changed the second element from a *string* matched with ``in`` to
# an explicit set of ids, because the string was an accident. ``"bjt" in name``
# only ever meant "a document whose corpus id starts with bjt-", and after the
# corpus grew past 27 documents that stopped being the set of correct answers:
# ``T6_BJT_Apuntes.md`` -- body: "apuntes del tema 6: el transistor bipolar
# como amplificador" -- is a *better* answer to a typo'd "transisto" than any
# datasheet, because it is the only document that contains the word. The
# fuzzy layer returns it first and that is right.
#
# So the label was widened, with a reason, and the threshold stayed at 0.80.
# Widening a label and lowering a threshold are not the same act: this one can
# still fail, and ``test_fuzzy_gate.py`` pins that every id listed here is a
# document whose text or name really does answer the query -- which is what
# stops a future corpus addition from quietly widening the gate for free.
ROBUST_QUERIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # prefix
    ("transisto", ("t6-abrev", "bjt-modelo", "transistor-bipolar")),
    # transposition, the classic typo
    ("transsistor", ("t6-abrev", "bjt-modelo", "transistor-bipolar")),
    # doubled letter
    ("transistorr", ("t6-abrev", "bjt-modelo", "transistor-bipolar")),
    # two edits
    ("transltor", ("t6-abrev", "bjt-modelo", "transistor-bipolar")),
    # accent folded away
    ("polarisacion", ("polarizacion-acentuada", "bjt-modelo", "bjt-notas")),
    # two words, one misspelled
    ("eberts moll", ("ebers-exacto", "ebers-lejos", "bjt-modelo", "codigo-ebers")),
    # transposition in a long word
    ("valensiana", ("paella",)),
    # one edit in a long word
    ("azarfan", ("paella",)),
    # doubled vowel
    ("sofritoo", ("paella",)),
    # doubled consonant
    ("azaarfann", ("paella",)),
)

# Measured, not assumed: these do not work, and the reason belongs in the
# report rather than in a passing test.
#   * a typo three edits away ("polirazcion" for "polarizacion") is outside
#     the design's distance budget, on purpose: raising the budget to catch
#     it would multiply false positives on short words.
#   * a query for a word that only appears in a *path* ("recettas" for
#     personal/recetas/paella.md) is out of scope because paths are
#     deliberately not fingerprinted; phase 026 measured that path trigrams
#     pollute similarity.
#   * "azaarfann" for "paella" doubles a vowel *and* a consonant and shares
#     almost nothing with the word. It is the one robust query the fuzzy layer
#     cannot answer at any threshold, and it was already failing before phase
#     045 -- the gate tolerates it because 0.80 of 10 is the bar, not 10 of
#     10. It is listed here so that it is a decision rather than a number that
#     happens to fit, and it stays in ROBUST_QUERIES' neighbourhood rather than
#     being removed: `test_fuzzy_gate.py` asserts the measured count.
OUT_OF_SCOPE = (
    ("polirazcion", "3 edits away: beyond the design's budget of 2"),
    ("recettas", "the word is only in the path, never in the content"),
    ("transistores", "already answered by the lexical engine"),
    ("azaarfann", "doubled vowel and doubled consonant; shares nothing with "
                   "paella. Measured: the fuzzy layer returns nothing for it"),
)

# Queries that must retrieve nothing, whatever the layer proposes.
MUST_BE_EMPTY = ("zzz no existe", "noexistenadaquienadie", "qqqzzz wwwyyy")

THRESHOLDS = {
    "T1_recall5_robust_queries": 0.80,
    "T2_leaks_on_must_be_empty": 0,
    "T3_lexical_mrr_unchanged": 0.0,
    "T4_storage_growth": 0.15,
    "T5_p95_added_ms": 8.0,
    "T6_new_runtime_dependencies": 0,
}


@dataclass
class Verdict:
    gate: str
    measured: float
    threshold: float
    passed: bool
    detail: str

    def line(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{status}  {self.gate:<34} {self.measured:>10.4f}"
            f"  (umbral {self.threshold})  {self.detail}"
        )


def _sync(path: Path) -> None:
    """Force the WAL into the main file so sizes are comparable."""
    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    # -- phase 047: the load check, owned by perf_gate ----------------------
    # This gate measures latency. It used to reach SHIP on a machine at 76% CPU
    # purely because it had enough margin, and it reached NO SHIP at 9.5 ms on
    # one at 88% -- neither verdict meaning anything about this product.
    #
    # Two readings, not one. Checking once, at the start, is fragile: a bursty
    # neighbour lets the machine read idle between bursts, which is exactly what
    # happened here (76% while starting, so the first check passed and the
    # measurement ran anyway). The check is repeated afterwards, and a busy
    # machine at either end withholds the verdict.
    #
    # `_cpu_load()` below used to be the whole of this gate's load awareness.
    # It only decorated a detail string; it could not change the verdict, which
    # is the only thing a reader of a gate cares about.
    from evaluation import perf_gate

    baseline_path = ROOT / "evaluation" / "fuzzy_baseline.json"
    previous = {}
    if baseline_path.exists():
        try:
            previous = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    strict = perf_gate.require_conclusive()
    load_check = perf_gate.load_gate(previous.get("calibration_best_s"))
    if not load_check.conclusive:
        print("=" * 100)
        print("PUERTA DE EVIDENCIA - 031 (busqueda difusa)")
        print("=" * 100)
        print(f"INCONCLUYENTE: {load_check.detail}")
        print("-" * 100)
        print("No se ha medido nada. Una cifra de latencia sobre una maquina")
        print("ocupada describe el trabajo de otro programa, asi que el")
        print("umbral no se toca y la corrida no cuenta.")
        return perf_gate.veto_exit(strict)
    corpus_module.assert_labels_are_consistent()
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-031-"))
    tree = workspace / "tree"
    corpus_module.build(tree)
    database = SearchDatabase(workspace / "index.db")
    Indexer(database).index_root(tree)
    mapping = corpus_module.ids_by_path(tree)
    lexical = SearchEngine(database)

    report = runner.measure(lexical, mapping)
    lexical_mrr = report.mrr()

    # The size baseline is taken with the *full* derived set in place, so the
    # fuzzy cost is measured against what the product actually stores, not
    # against a half-built database.
    SemanticIndex(database).rebuild()
    _sync(workspace / "index.db")
    size_before = (workspace / "index.db").stat().st_size

    index = FuzzyIndex(database)
    index.rebuild()
    rows = index.row_count()
    _sync(workspace / "index.db")
    size_after = (workspace / "index.db").stat().st_size

    fuzzy = FuzzySearchEngine(lexical, index)

    # -- T1: recall on the robust queries ------------------------------------
    hits = 0
    detail_rows = []
    for query, expected in ROBUST_QUERIES:
        ranked = [
            mapping.get(Path(result.path).resolve(), result.path)
            for result in fuzzy.search(query, limit=5)
        ]
        lexical_hits = [
            mapping.get(Path(result.path).resolve(), result.path)
            for result in lexical.search(query, limit=5)
        ]
        found = bool(set(expected) & set(ranked))
        hits += bool(found)
        detail_rows.append({
            "query": query,
            "lexical": lexical_hits,
            "fuzzy": ranked,
            "expected": list(expected),
            "found": found,
        })
    recall5 = hits / len(ROBUST_QUERIES)

    # -- T2: nothing invented ------------------------------------------------
    leaks = []
    for query in MUST_BE_EMPTY:
        ranked = fuzzy.search(query, limit=5)
        if ranked:
            leaks.append({
                "query": query,
                "results": [r.name for r in ranked],
            })

    # -- T3: the lexical engine is untouched --------------------------------
    report_after = runner.measure(lexical, mapping)
    mrr_delta = abs(report_after.mrr() - lexical_mrr)

    # -- T4: storage --------------------------------------------------------
    fuzzy_bytes = size_after - size_before
    growth = (fuzzy_bytes / size_before) if size_before else 0.0

    # -- T5: latency of a query that finds nothing --------------------------
    # Measured as the difference between two *independent* p95 values, not as
    # the p95 of paired differences. The first version of this gate did the
    # latter and reported 41 ms, then 33 ms, then 16 ms while the layer's real
    # cost was falling — because on a loaded machine the p95 of the
    # difference of two noisy samples is dominated by noise, not by the layer.
    # Absolute p95s per engine are also recorded, and the machine's load, so
    # the number can be interpreted instead of merely believed.
    lexical_p50, lexical_p95, fuzzy_p50, fuzzy_p95, load = _latency(
        lexical, fuzzy, MUST_BE_EMPTY
    )
    added = fuzzy_p95 - lexical_p95

    # -- T6: dependencies ----------------------------------------------------
    declared = _runtime_dependencies()
    allowed = {"pypdf", "watchdog"}
    new_dependencies = len(declared - allowed)

    verdicts = [
        Verdict("T1 recall@5 robust queries", recall5,
                THRESHOLDS["T1_recall5_robust_queries"],
                recall5 >= THRESHOLDS["T1_recall5_robust_queries"],
                f"{hits}/{len(ROBUST_QUERIES)} consultas"),
        Verdict("T2 leaks on must-be-empty", len(leaks),
                THRESHOLDS["T2_leaks_on_must_be_empty"],
                not leaks,
                "ninguna consulta recibio resultados" if not leaks else str(leaks)),
        Verdict("T3 lexical MRR change", mrr_delta,
                THRESHOLDS["T3_lexical_mrr_unchanged"],
                mrr_delta == 0.0,
                f"MRR sigue en {lexical_mrr:.4f}"),
        Verdict("T4 index storage growth", growth,
                THRESHOLDS["T4_storage_growth"],
                growth <= THRESHOLDS["T4_storage_growth"],
                f"{fuzzy_bytes:,} B sobre {size_before:,} B, "
                f"{rows} filas ({rows / max(1, len(corpus_module.DOCUMENTS)):.0f} "
                f"por documento, tope {MAX_TRIGRAMS_PER_DOC})"),
        Verdict("T5 added p95 (ms)", added,
                THRESHOLDS["T5_p95_added_ms"],
                added <= THRESHOLDS["T5_p95_added_ms"],
                f"lexico p50={lexical_p50:.2f} p95={lexical_p95:.2f} | "
                f"hibrido p50={fuzzy_p50:.2f} p95={fuzzy_p95:.2f} | "
                f"carga de CPU {load}%"),
        Verdict("T6 new runtime dependencies", new_dependencies,
                THRESHOLDS["T6_new_runtime_dependencies"],
                new_dependencies == 0,
                f"declaradas={sorted(declared)}"),
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 031 (busqueda robusta)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("detalle por consulta (T1):")
    for row in detail_rows:
        mark = "ok " if row["found"] else "FALLO"
        print(f"  {mark} {row['query']!r:>18} -> {row['fuzzy']}")
        if not row["lexical"]:
            print("       (lexico: nada, como se esperaba)")
    payload = {
        "phase": "031",
        "load_before": load_check.detail,
        "calibration_best_s": load_check.calibration_best_s,
        "verdicts": [
            {
                "gate": verdict.gate,
                "measured": verdict.measured,
                "threshold": verdict.threshold,
                "passed": verdict.passed,
                "detail": verdict.detail,
            }
            for verdict in verdicts
        ],
        "robust_queries": detail_rows,
        "out_of_scope": [
            {"query": query, "reason": reason} for query, reason in OUT_OF_SCOPE
        ],
        "leaks": leaks,
        "rows": rows,
        "documents": len(corpus_module.DOCUMENTS),
    }
    # The machine may have become busy *during* the measurement, which is the
    # common case with a bursty neighbour: the first reading of this fix caught
    # the machine idle, and the measurement ran on a CPU another program had
    # claimed by the time it finished. A verdict computed before checking again
    # would publish a latency figure measured under conditions nobody declared.
    after = perf_gate.load_gate()
    payload["load_after"] = after.detail

    out = ROOT / "evaluation" / "fuzzy_baseline.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")

    if not after.conclusive:
        print("-" * 100)
        print(f"INCONCLUYENTE despues de medir: {after.detail}")
        return perf_gate.veto_exit(strict)
    if load_check.conclusive and load_check.detail != after.detail:
        print(f"carga antes de medir:  {load_check.detail}")
        print(f"carga despues de medir: {after.detail}")

    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


def _cpu_load() -> str:
    """CPU load as a percentage, because a loaded machine cannot conclude."""
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).LoadPercentage"],
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout.strip() or "?"
    except Exception:
        try:
            import os

            return str(int(os.getloadavg()[0]))
        except (AttributeError, OSError):
            return "?"


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    position = max(0, int(round(fraction * len(ordered))) - 1)
    return ordered[position]


def _latency(lexical, fuzzy, queries, samples: int = 80) -> tuple:
    """p50/p95 of each engine, measured independently, plus the CPU load."""
    import time as _time

    def run(engine, query) -> None:
        engine.search(query, limit=5)

    # A warm-up pass keeps first-call import and page-cache costs out of both
    # distributions, so the comparison is between the two engines and not
    # between "first call" and "rest".
    for query in queries:
        run(lexical, query)
        run(fuzzy, query)

    lexical_samples: list[float] = []
    fuzzy_samples: list[float] = []
    for _ in range(samples):
        for query in queries:
            started = _time.perf_counter()
            run(lexical, query)
            lexical_samples.append((_time.perf_counter() - started) * 1000)
    for _ in range(samples):
        for query in queries:
            started = _time.perf_counter()
            run(fuzzy, query)
            fuzzy_samples.append((_time.perf_counter() - started) * 1000)

    load = _cpu_load()
    return (
        _percentile(lexical_samples, 0.50),
        _percentile(lexical_samples, 0.95),
        _percentile(fuzzy_samples, 0.50),
        _percentile(fuzzy_samples, 0.95),
        load,
    )


def _runtime_dependencies() -> set[str]:
    import re
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        re.split(r"[<>=!\[ ]", entry, maxsplit=1)[0].strip().lower()
        for entry in data["project"]["dependencies"]
    }


if __name__ == "__main__":
    raise SystemExit(main())
