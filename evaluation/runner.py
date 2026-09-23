"""Run the labelled corpus through the real search engine (spec 013).

The evaluation deliberately goes through :class:`SearchEngine` and SQLite
— not through the ranker in isolation — so what is measured is the ranking
a user actually gets: FTS5 retrieval, the candidate pool, every signal and
the tie-breaking.

Run it with ``python -m evaluation``; tests call :func:`run` directly and
compare against ``evaluation/baseline.json`` (the regression fixture).
"""

import json
import shutil
import tempfile
from pathlib import Path

from evaluation import corpus as corpus_module
from evaluation.metrics import EvaluationReport, evaluate
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import Ranker
from universal_search.index.search import SearchEngine

DEFAULT_LIMIT = 10
K_VALUES = (1, 3, 5)


def _index(
    tree: Path, database_path: Path, ranker: Ranker | None = None
) -> SearchEngine:
    Indexer(SearchDatabase(database_path)).index_root(tree)
    return SearchEngine(SearchDatabase(database_path), ranker=ranker)


def measure(
    engine: SearchEngine,
    ids_by_path: dict[Path, str],
    *,
    limit: int = DEFAULT_LIMIT,
    k_values: tuple[int, ...] = K_VALUES,
) -> EvaluationReport:
    """Search every labelled query and score the returned document ids."""
    measured: list[
        tuple[str, list[str], frozenset[str], list[float], list[dict[str, float]]]
    ] = []
    for labelled in corpus_module.LABELLED_QUERIES:
        # explain=True keeps the per-signal breakdown of every result, so
        # a measurement can be traced back to the signals that produced it.
        results = engine.search(labelled.query, limit=limit, explain=True)
        ranked = [
            ids_by_path.get(Path(result.path).resolve(), result.path)
            for result in results
        ]
        measured.append(
            (labelled.query, ranked, labelled.relevant,
             [result.score for result in results],
             [result.explain or {} for result in results])
        )
    return evaluate(measured, k_values)


def render(report: EvaluationReport, *, top: int = 3) -> str:
    """ASCII-only report: one line per query plus the aggregates."""
    lines: list[str] = []
    for score in report.scores:
        ranked = ", ".join(score.ranked[:top]) or "(sin resultados)"
        reciprocal = (
            f"{score.reciprocal_rank:.3f}"
            if score.relevant
            else ("ok" if not score.ranked else "FALLO")
        )
        margin = score.relevance_margin()
        margin_text = "  n/a" if margin is None else f"{margin:+.3f}"
        lines.append(
            f"{score.query:<24} rr={reciprocal:<6} margin={margin_text:<7} "
            f"top{top}=[{ranked}]"
        )
    width = 68
    lines.append("-" * width)
    for k in report.k_values:
        lines.append(
            f"mean P@{k}={report.mean_precision(k):.3f}  "
            f"mean R@{k}={report.mean_recall(k):.3f}"
        )
    lines.append(f"MRR={report.mrr():.3f}  queries={len(report.scores)}")
    return "\n".join(lines)


def run(
    *,
    limit: int = DEFAULT_LIMIT,
    k_values: tuple[int, ...] = K_VALUES,
    top: int = 3,
    json_out: Path | None = None,
    keep: bool = False,
    write_fixture: Path | None = None,
    ranker: Ranker | None = None,
) -> dict:
    """Index the corpus, measure every labelled query, print the report.

    ``write_fixture`` stores the per-query ranking as the regression
    baseline; ``keep`` leaves the temporary corpus on disk for debugging;
    ``ranker`` replaces the default weighting, which is how a candidate
    change is evaluated before being adopted.
    """
    corpus_module.assert_labels_are_consistent()
    temp = Path(tempfile.mkdtemp(prefix="universal-search-eval-"))
    tree = temp / "tree"
    try:
        corpus_module.build(tree)
        engine = _index(tree, temp / "index.db", ranker)
        report = measure(
            engine, corpus_module.ids_by_path(tree), limit=limit, k_values=k_values
        )
        payload = report.as_dict()
        payload["limit"] = limit
        print(render(report, top=top))
        if json_out is not None:
            json_out.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if write_fixture is not None:
            fixture = {
                "limit": limit,
                "k_values": list(k_values),
                "top": {
                    score.query: list(score.ranked[:3])
                    for score in report.scores
                },
                "aggregates": {
                    "mrr": report.mrr(),
                    "mean_precision_at_k": {
                        str(k): report.mean_precision(k) for k in k_values
                    },
                    "mean_recall_at_k": {
                        str(k): report.mean_recall(k) for k in k_values
                    },
                },
            }
            write_fixture.write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        if not keep:
            shutil.rmtree(temp, ignore_errors=True)
    return payload
