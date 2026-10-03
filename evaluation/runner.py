"""Run the labelled corpus through the real search engine (spec 013).

The evaluation deliberately goes through :class:`SearchEngine` and SQLite
— not through the ranker in isolation — so what is measured is the ranking
a user actually gets: FTS5 retrieval, the candidate pool, every signal and
the tie-breaking.

Run it with ``python -m evaluation``; tests call :func:`run` directly and
compare against ``evaluation/baseline.json`` (the regression fixture).
"""

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from evaluation import corpus as corpus_module
from evaluation import metrics as metrics_module
from evaluation.metrics import EvaluationReport, evaluate
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import Ranker
from universal_search.index.search import SearchEngine
from universal_search.query import parse_query, translate

DEFAULT_LIMIT = 10
# Phase 045: P@1/5/10 and R@5/10 are the numbers this phase reports.
K_VALUES = metrics_module.DEFAULT_K_VALUES


def _index(
    tree: Path, database_path: Path, ranker: Ranker | None = None
) -> SearchEngine:
    Indexer(SearchDatabase(database_path)).index_root(tree)
    return SearchEngine(SearchDatabase(database_path), ranker=ranker)


def corpus_hash(tree: Path) -> str:
    """SHA-256 over every corpus file: relative path, bytes, mtime, size.

    Deterministic by construction (the corpus module fixes every byte and
    every timestamp), so two runs over the same corpus definition produce
    the same hash — which is what makes a baseline attributable to the exact
    corpus it was measured on (phase 026).
    """
    digest = hashlib.sha256()
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(str(path.relative_to(tree)).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\x00")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def query_text_part(query: str) -> str:
    """The free-text part of a query (filters removed), casefolded.

    Exact-match correctness is about the text a user is looking for, so
    ``bjt type:txt`` is judged on ``bjt`` and ``type:pdf`` (no text) is
    excluded.
    """
    plan = translate(parse_query(query))
    return " ".join(plan.terms).casefold()


def exact_targets(query: str, documents) -> frozenset[str]:
    """Ids whose name/path/content contains the query text verbatim.

    A query is an *exact* query when its text part appears verbatim (case
    folded) in some document's name, path or content — e.g. ``informe`` is a
    filename, ``"ebers moll"`` is a content phrase, ``punto Q`` is a content
    phrase. Those are the queries where an exact document *must* rank first.
    """
    text = query_text_part(query)
    if not text:
        return frozenset()
    targets: set[str] = set()
    for document in documents:
        name = Path(document.path).name
        haystack = f"{name} {document.path} {document.content or ''}".casefold()
        if text in haystack:
            targets.add(document.id)
    return frozenset(targets)


def exact_match_summary(
    report: EvaluationReport, documents
) -> tuple[float, list[dict[str, object]]]:
    """Fraction of exact queries whose top-1 result is an exact document.

    Returns the correctness (1.0 when every exact query is answered by an
    exact document at rank 1) and one row per exact query for the report.
    """
    rows: list[dict[str, object]] = []
    for score in report.scores:
        targets = exact_targets(score.query, documents)
        if not targets:
            continue
        top = score.ranked[0] if score.ranked else None
        rows.append(
            {
                "query": score.query,
                "exact_targets": sorted(targets),
                "top": top,
                "correct": top in targets,
            }
        )
    correctness = (
        sum(1 for row in rows if row["correct"]) / len(rows) if rows else 1.0
    )
    return correctness, rows


def failure_inventory(
    report: EvaluationReport, labelled_queries
) -> list[dict[str, object]]:
    """Every query that misses relevant documents, worst first.

    A *total* failure retrieves nothing relevant; a *partial* failure
    retrieves some but not all. This is the concrete synonym/paraphrase
    inventory the phase exists to measure.
    """
    by_query = report.by_query()
    rows: list[dict[str, object]] = []
    for labelled in labelled_queries:
        score = by_query[labelled.query]
        retrieved = sum(1 for rid in score.ranked if rid in score.relevant)
        total = len(score.relevant)
        if retrieved == total:
            continue
        rows.append(
            {
                "query": labelled.query,
                "failure_class": labelled.failure_class or "unknown",
                "retrieved": retrieved,
                "relevant": total,
                "kind": "total" if retrieved == 0 else "partial",
                "missing": list(score.missing()),
            }
        )
    rows.sort(key=lambda row: (row["retrieved"], -int(row["relevant"])))
    return rows


def measure(
    engine: SearchEngine,
    ids_by_path: dict[Path, str],
    *,
    limit: int = DEFAULT_LIMIT,
    k_values: tuple[int, ...] = K_VALUES,
    latency: dict[str, float] | None = None,
) -> EvaluationReport:
    """Search every labelled query and score the returned document ids.

    ``latency`` (phase 026), when given, is filled with the wall-clock
    milliseconds of every query. Latency is informational — it depends on
    the machine — so it is recorded in the baseline report but never
    compared by the regression fixture.
    """
    measured: list[
        tuple[str, list[str], frozenset[str], list[float], list[dict[str, float]]]
    ] = []
    for labelled in corpus_module.LABELLED_QUERIES:
        # explain=True keeps the per-signal breakdown of every result, so
        # a measurement can be traced back to the signals that produced it.
        started = time.perf_counter()
        results = engine.search(labelled.query, limit=limit, explain=True)
        if latency is not None:
            latency[labelled.query] = (time.perf_counter() - started) * 1000
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


def build_semantic_baseline(
    *,
    report: EvaluationReport,
    tree: Path,
    limit: int,
    k_values: tuple[int, ...],
    latency: dict[str, float],
    decision: dict[str, object] | None = None,
) -> dict:
    """Assemble the phase-026 semantic baseline record.

    Everything here is derived from the measured report and the corpus on
    disk, so the record is reproducible from the corpus definition alone —
    except ``latency_ms``, which is machine-dependent and marked
    informational. ``decision`` (filled after the evidence gate) records
    the threshold and the outcome.
    """
    correctness, exact_rows = exact_match_summary(
        report, corpus_module.DOCUMENTS
    )
    payload: dict[str, object] = {
        "phase": "026",
        "engine": "lexical",
        "corpus_hash": corpus_hash(tree),
        "documents": len(corpus_module.DOCUMENTS),
        "queries": len(report.scores),
        "k_values": list(k_values),
        "limit": limit,
        "metrics": {
            "mean_precision_at_k": {
                str(k): report.mean_precision(k) for k in k_values
            },
            "mean_recall_at_k": {
                str(k): report.mean_recall(k) for k in k_values
            },
            "mrr": report.mrr(),
        },
        "exact_match_correctness": correctness,
        "exact_queries": exact_rows,
        "failures": failure_inventory(report, corpus_module.LABELLED_QUERIES),
        "latency_ms": {
            "informational": True,
            "mean": sum(latency.values()) / len(latency) if latency else 0.0,
            "max": max(latency.values()) if latency else 0.0,
            "per_query": dict(sorted(latency.items())),
        },
    }
    if decision is not None:
        payload["decision"] = decision
    return payload


def write_semantic_baseline(path: Path, payload: dict) -> None:
    """Write the phase-026 baseline record as JSON."""
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(
    *,
    limit: int = DEFAULT_LIMIT,
    k_values: tuple[int, ...] = K_VALUES,
    top: int = 3,
    json_out: Path | None = None,
    keep: bool = False,
    write_fixture: Path | None = None,
    ranker: Ranker | None = None,
    semantic_baseline: Path | None = None,
    decision: dict[str, object] | None = None,
) -> dict:
    """Index the corpus, measure every labelled query, print the report.

    ``write_fixture`` stores the per-query ranking as the regression
    baseline; ``keep`` leaves the temporary corpus on disk for debugging;
    ``ranker`` replaces the default weighting, which is how a candidate
    change is evaluated before being adopted. ``semantic_baseline``
    (phase 026) writes the full measurement record — corpus hash, metrics,
    exact-match correctness, latency and the failure inventory — and
    ``decision`` attaches the evidence-gate outcome to it.
    """
    corpus_module.assert_labels_are_consistent()
    temp = Path(tempfile.mkdtemp(prefix="universal-search-eval-"))
    tree = temp / "tree"
    try:
        corpus_module.build(tree)
        engine = _index(tree, temp / "index.db", ranker)
        latency: dict[str, float] = {}
        report = measure(
            engine, corpus_module.ids_by_path(tree), limit=limit,
            k_values=k_values, latency=latency,
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
        if semantic_baseline is not None:
            write_semantic_baseline(
                semantic_baseline,
                build_semantic_baseline(
                    report=report, tree=tree, limit=limit, k_values=k_values,
                    latency=latency, decision=decision,
                ),
            )
    finally:
        if not keep:
            shutil.rmtree(temp, ignore_errors=True)
    return payload
