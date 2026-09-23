"""Scale, concurrency, interruption and cache behaviour (phase 011).

Covers the spec-011 test matrix: large batches, concurrent search and
indexing, interrupted work, cache invalidation, deterministic results and
resource handling. Timings use generous tripwires (catastrophic
regressions only) so the suite stays reliable on slow CI machines.
"""

import threading
import time
from pathlib import Path

import pytest

from universal_search.index import indexer as indexer_module
from universal_search.index import ranking
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import COMMIT_EVERY, Indexer
from universal_search.index.search import SearchEngine


def build_tree(root: Path, count: int, seed: str = "bjt cmos mux ebers") -> list[Path]:
    """Deterministic corpus: every file matches the test queries."""
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index in range(count):
        folder = root / ("left" if index % 2 else "right")
        folder.mkdir(exist_ok=True)
        path = folder / f"doc-{index:04d}.txt"
        path.write_text(
            f"{seed} {seed} variant {index} fourier laplace", encoding="utf-8"
        )
        written.append(path)
    return written


def test_large_batch_indexed_with_batched_commits(tmp_path):
    tree = tmp_path / "tree"
    build_tree(tree, 600)  # 3 x COMMIT_EVERY: crosses every commit boundary
    assert 600 > 2 * COMMIT_EVERY
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)

    started = time.perf_counter()
    stats = indexer.index_root(tree)
    elapsed = time.perf_counter() - started
    indexer.close()

    assert stats.created == 600
    assert stats.errors == 0
    # Catastrophic-regression tripwire only (measured ~1.5 s locally).
    assert elapsed < 60

    connection = database.connect()
    documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    fts_rows = connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    connection.close()
    assert documents == 600
    assert fts_rows == 600


def test_incremental_pass_materially_cheaper_than_build(tmp_path):
    tree = tmp_path / "tree"
    build_tree(tree, 400)

    warm_indexer = Indexer(SearchDatabase(tmp_path / "warm.db"))
    warm_indexer.index_root(tree)
    started = time.perf_counter()
    warm_stats = warm_indexer.index_root(tree)
    warm = time.perf_counter() - started
    warm_indexer.close()

    cold_indexer = Indexer(SearchDatabase(tmp_path / "cold.db"))
    started = time.perf_counter()
    cold_indexer.index_root(tree)
    cold = time.perf_counter() - started
    cold_indexer.close()

    assert warm_stats.unchanged == 400
    assert warm_stats.created == 0
    # Profiled ~8x; the factor of 2 keeps CI jitter harmless.
    assert warm * 2 < cold


def test_search_runs_while_indexing(tmp_path):
    tree = tmp_path / "tree"
    paths = build_tree(tree, 120)
    database_path = tmp_path / "index.db"
    Indexer(SearchDatabase(database_path)).index_root(tree)
    # Change every file so the concurrent pass performs real writes.
    for path in paths:
        path.write_text(
            path.read_text(encoding="utf-8") + " revision", encoding="utf-8"
        )

    errors: list[BaseException] = []
    writer_done = threading.Event()

    def writer() -> None:
        try:
            Indexer(SearchDatabase(database_path)).index_root(tree)
        except BaseException as exc:  # noqa: BLE001 - the test collects them
            errors.append(exc)
        finally:
            writer_done.set()

    def reader() -> None:
        engine = SearchEngine(SearchDatabase(database_path))
        try:
            while not writer_done.is_set():
                engine.search("bjt fourier", limit=5)
                engine.search("no_such_token_zzz", limit=5)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(2)]
    for thread in readers:
        thread.start()
    time.sleep(0.05)  # let readers spin up before writes begin
    write_thread = threading.Thread(target=writer)
    write_thread.start()
    write_thread.join(timeout=120)
    writer_done.set()
    for thread in readers:
        thread.join(timeout=30)

    assert errors == []
    final = SearchEngine(SearchDatabase(database_path))
    results = final.search("bjt", limit=200)
    assert len(results) == 120  # every document still found, no duplicates


def test_interrupted_indexing_resumes_without_duplicates(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    build_tree(tree, 600)
    database = SearchDatabase(tmp_path / "index.db")
    original_scan = indexer_module.scan_local
    seen = {"count": 0}

    def failing_scan(root, rules):
        for item in original_scan(root, rules):
            seen["count"] += 1
            if seen["count"] > 300:
                raise RuntimeError("simulated crash")
            yield item

    monkeypatch.setattr(indexer_module, "scan_local", failing_scan)
    with pytest.raises(RuntimeError, match="simulated crash"):
        Indexer(database).index_root(tree)

    # Durable prefix: exactly the committed batches survived; the open
    # transaction rolled back when the connection died.
    connection = database.connect()
    partial = connection.execute(
        "SELECT COUNT(*) FROM documents"
    ).fetchone()[0]
    connection.close()
    assert partial == COMMIT_EVERY

    monkeypatch.setattr(indexer_module, "scan_local", original_scan)
    resume_stats = Indexer(database).index_root(tree)
    assert resume_stats.created == 600 - partial

    connection = database.connect()
    total, distinct = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT path) FROM documents"
    ).fetchone()
    fts_rows = connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    connection.close()
    assert total == distinct == fts_rows == 600
    assert SearchEngine(SearchDatabase(tmp_path / "index.db")).search("bjt")


def test_ranking_caches_bounded_and_invalidatable(monkeypatch):
    # Identical content is tokenized exactly once (cache hit is identity).
    first = ranking.content_words("alpha beta gamma delta")
    second = ranking.content_words("alpha beta gamma delta")
    assert first is second

    # The content cache never exceeds its cap, even when the cap is tiny.
    monkeypatch.setattr(ranking, "_CONTENT_WORDS_CAP", 2)
    for index in range(6):
        ranking.content_words(f"unique content number {index} words here")
    assert len(ranking._CONTENT_WORDS) <= 2

    # Populate every lru-backed cache, then invalidate all of them.
    ranking._parse_iso("2026-01-02T03:04:05")
    ranking._name_parts("Report.pdf")
    ranking._path_component_tokens("a/b/Report.pdf")
    assert ranking._parse_iso.cache_info().currsize >= 1

    ranking.clear_caches()
    assert len(ranking._CONTENT_WORDS) == 0
    for cached in (
        ranking._parse_iso,
        ranking._name_parts,
        ranking._path_component_tokens,
    ):
        assert cached.cache_info().currsize == 0


def test_results_deterministic_across_engines_and_cache_states(tmp_path):
    tree = tmp_path / "tree"
    build_tree(tree, 150)
    database_path = tmp_path / "index.db"
    Indexer(SearchDatabase(database_path)).index_root(tree)

    ranking.clear_caches()  # cold start
    cold = SearchEngine(SearchDatabase(database_path)).search(
        "bjt fourier", limit=20
    )
    warm = SearchEngine(SearchDatabase(database_path)).search(
        "bjt fourier", limit=20
    )
    again = SearchEngine(SearchDatabase(database_path)).search(
        "bjt fourier", limit=20
    )

    assert len(cold) == 20
    assert [r.path for r in cold] == [r.path for r in warm] == [r.path for r in again]
    assert [round(r.score, 9) for r in cold] == [round(r.score, 9) for r in warm]


def test_indexer_resource_handling_and_maintenance(tmp_path):
    database = SearchDatabase(tmp_path / "index.db")
    indexer = Indexer(database)

    # The write connection is reused, close() is idempotent, and a closed
    # indexer transparently reopens on demand.
    assert indexer.connection() is indexer.connection()
    indexer.close()
    indexer.close()
    reopened = indexer.connection()
    assert reopened.execute("SELECT 1").fetchone()[0] == 1
    indexer.close()

    # Maintenance: full checkpoint + vacuum, everything closed afterwards.
    sizes = database.maintenance(vacuum=True)
    for key in ("database", "wal", "total", "freelist_after", "pages"):
        assert key in sizes
    assert sizes["database"] > 0
    assert sizes["wal"] == 0  # TRUNCATE checkpoint + last-close cleanup
    assert sizes["freelist_after"] == 0  # vacuum reclaimed every free page
    assert sizes["freelist_after"] <= sizes["freelist_before"]
