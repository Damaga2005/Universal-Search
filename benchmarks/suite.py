"""Benchmark measurements (phase 011) — ASCII output only.

Covers the spec's required baselines over a deterministic synthetic tree:

* initial indexing, incremental indexing, single-document update,
  deletion reconciliation
* search latency (cold and warm, per query shape)
* startup / database open
* memory during indexing and during search (``memory=True``; the
  tracemalloc overhead only affects the run that asks for it)

Kept out of ordinary CI by design: run it from the repo root with
``python -m benchmarks [--profile N] [--memory] [--json out.json]``.
"""

import json
import shutil
import tempfile
import time
import tracemalloc
from pathlib import Path

from benchmarks import corpus
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine

QUERIES = (
    "bjt",                          # single common term
    "mux cmos",                     # multi-term AND
    '"ebers moll"',                 # exact phrase
    "path:universidad bjt",         # structured filter
    "zzznotfound",                  # no-result worst case
)

# Warm repetitions per query: 30 gives a usable p95 without inflating
# runtime (roughly 5 s at the measured ~35 ms mean).
WARM_REPETITIONS = 30


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


def _latency_block(engine: SearchEngine) -> dict[str, float]:
    """Cold first query + warm stats over the query set."""
    cold_start = time.perf_counter()
    engine.search(QUERIES[0], limit=20)
    cold_ms = (time.perf_counter() - cold_start) * 1000

    samples: list[float] = []
    for query in QUERIES:
        for _ in range(WARM_REPETITIONS):
            started = time.perf_counter()
            engine.search(query, limit=20)
            samples.append((time.perf_counter() - started) * 1000)
    mean = sum(samples) / len(samples)
    return {
        "cold_ms": cold_ms,
        "mean_ms": mean,
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
        "samples": len(samples),
    }


def run(
    profile: int,
    *,
    memory: bool = False,
    json_out: Path | None = None,
    keep: bool = False,
) -> dict:
    """Run every benchmark over ``profile`` synthetic documents."""
    temp = Path(tempfile.mkdtemp(prefix=f"universal-search-bench-{profile}-"))
    tree = temp / "tree"
    results: dict[str, object] = {"profile": profile, "memory": memory}
    try:
        # -- corpus -------------------------------------------------------
        started = time.perf_counter()
        files = corpus.build(tree, profile)
        results["corpus_write_ms"] = (time.perf_counter() - started) * 1000

        database_path = temp / "index.db"
        database = SearchDatabase(database_path)
        indexer = Indexer(database)

        # -- startup / database open --------------------------------------
        opens = []
        for _ in range(10):
            probe = SearchDatabase(database_path)
            started = time.perf_counter()
            connection = probe.connect()
            connection.close()
            opens.append((time.perf_counter() - started) * 1000)
        results["db_open_mean_ms"] = sum(opens) / len(opens)
        results["db_open_max_ms"] = max(opens)

        # -- initial indexing ---------------------------------------------
        if memory:
            tracemalloc.start()
        started = time.perf_counter()
        stats = indexer.index_root(tree)
        results["initial_index_s"] = time.perf_counter() - started
        if memory:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results["index_peak_mib"] = peak / (1024 * 1024)
        results["initial_stats"] = stats.as_dict()

        # -- incremental (no changes) -------------------------------------
        started = time.perf_counter()
        stats = indexer.index_root(tree)
        results["incremental_s"] = time.perf_counter() - started
        results["incremental_stats"] = stats.as_dict()

        # -- single-document update ---------------------------------------
        corpus.modify(files[:1], 1)
        started = time.perf_counter()
        stats = indexer.index_root(tree)
        results["single_update_s"] = time.perf_counter() - started
        results["single_update_stats"] = stats.as_dict()

        # -- bulk update (1% of the corpus) --------------------------------
        bulk = max(1, profile // 100)
        corpus.modify(files[1 : 1 + bulk], bulk)
        started = time.perf_counter()
        stats = indexer.index_root(tree)
        results["bulk_update_s"] = time.perf_counter() - started
        results["bulk_update_stats"] = stats.as_dict()

        # -- deletion reconciliation ---------------------------------------
        corpus.delete(files, bulk)
        started = time.perf_counter()
        stats = indexer.index_root(tree)
        results["deletion_s"] = time.perf_counter() - started
        results["deletion_stats"] = stats.as_dict()

        # -- search latency + memory ---------------------------------------
        engine = SearchEngine(SearchDatabase(database_path))
        if memory:
            tracemalloc.start()
        results["search"] = _latency_block(engine)
        if memory:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results["search_peak_mib"] = peak / (1024 * 1024)

        # -- growth ---------------------------------------------------------
        results["db_sizes"] = database.sizes()

        _print(results)
        if json_out is not None:
            json_out.write_text(
                json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return results
    finally:
        if keep:
            print(f"(kept {temp})")
        else:
            shutil.rmtree(temp, ignore_errors=True)


def _print(results: dict) -> None:
    search = results["search"]
    assert isinstance(search, dict)
    rows = [
        ("profile", results["profile"], "docs"),
        ("corpus write", round(results["corpus_write_ms"], 1), "ms"),
        ("db open (mean)", round(results["db_open_mean_ms"], 2), "ms"),
        ("initial indexing", round(results["initial_index_s"], 2), "s"),
        ("incremental pass", round(results["incremental_s"], 3), "s"),
        ("single-doc update", round(results["single_update_s"], 3), "s"),
        (f"bulk update ({results['bulk_update_stats']['updated']} docs)",
         round(results["bulk_update_s"], 3), "s"),
        (f"deletion reconcile ({results['deletion_stats']['deleted']} docs)",
         round(results["deletion_s"], 3), "s"),
        ("search cold", round(search["cold_ms"], 1), "ms"),
        ("search mean", round(search["mean_ms"], 2), "ms"),
        ("search p50", round(search["p50_ms"], 2), "ms"),
        ("search p95", round(search["p95_ms"], 2), "ms"),
        ("search max", round(search["max_ms"], 2), "ms"),
        ("search samples", search["samples"], "n"),
    ]
    if "index_peak_mib" in results:
        rows.append(("index peak mem", round(results["index_peak_mib"], 1), "MiB"))
        rows.append(("search peak mem", round(results["search_peak_mib"], 1), "MiB"))
    sizes = results["db_sizes"]
    rows.append(("index size", round(sizes["total"] / (1024 * 1024), 1), "MiB"))
    print()
    print(f"== benchmark profile {results['profile']} ==")
    for label, value, unit in rows:
        print(f"{label:<28} {value:>10} {unit}")
