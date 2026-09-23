"""Local metrics: bounded buffers, core wiring, robust sink (phase 011).

The invariant that matters most: records never contain query text, paths
or document content — counters and durations only.
"""

import inspect
from pathlib import Path

import pytest

from universal_search import metrics
from universal_search.appconfig import AppPaths
from universal_search.gui.services import SearchService
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


@pytest.fixture(autouse=True)
def isolate_metrics():
    metrics.reset()
    yield
    metrics.reset()


def _tree(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (root / f"doc-{index}.txt").write_text(
            f"bjt mux document number {index}", encoding="utf-8"
        )


def test_search_records_latency_and_result_count(tmp_path):
    engine = SearchEngine(SearchDatabase(tmp_path / "index.db"))
    results = engine.search("bjt")

    records = metrics.records()
    assert records, "the engine must record every executed search"
    last = records[-1]
    assert last["kind"] == "search"
    assert last["results"] == len(results)
    assert last["latency_ms"] >= 0
    assert isinstance(last["seq"], int)
    assert last["at"]


def test_records_never_carry_query_text(tmp_path):
    parameters = inspect.signature(
        metrics.MetricsCollector.record_search
    ).parameters
    assert set(parameters) == {"self", "latency_ms", "results"}  # no query slot

    engine = SearchEngine(SearchDatabase(tmp_path / "index.db"))
    engine.search("supersecretterm")
    serialized = str(metrics.records())
    assert "supersecretterm" not in serialized


def test_ring_buffer_bounded_and_dropped_counted():
    collector = metrics.MetricsCollector(history=4)
    for index in range(10):
        collector.record_search(float(index), index)

    records = collector.records()
    assert len(records) == 4
    assert records[-1]["seq"] == 10
    summary = collector.summary()
    assert summary["dropped"] == 6
    assert summary["buffered"] == 10
    assert summary["history"] == 4


def test_summary_arithmetic():
    collector = metrics.MetricsCollector()
    collector.record_search(10.0, 2)
    collector.record_search(20.0, 0)
    collector.record_search(30.0, 6)

    summary = collector.summary()
    assert summary["searches"] == 3
    assert summary["latency_mean_ms"] == 20.0
    assert summary["latency_last_ms"] == 30.0
    assert summary["latency_max_ms"] == 30.0
    assert summary["results_total"] == 8
    assert summary["index_passes"] == 0
    assert summary["last_index"] is None


def test_index_pass_records_duration_stats_and_db_writes(tmp_path):
    tree = tmp_path / "tree"
    _tree(tree, 25)

    stats = Indexer(SearchDatabase(tmp_path / "index.db")).index_root(tree)

    records = metrics.records()
    last = records[-1]
    assert last["kind"] == "index"
    assert last["duration_s"] > 0
    assert last["db_writes"] > 0
    assert last["created"] == stats.created == 25
    summary = metrics.summary()
    assert summary["index_passes"] == 1
    assert summary["last_index"]["db_writes"] == last["db_writes"]


def test_search_flushes_and_index_pass_appends_to_sink(tmp_path):
    sink = tmp_path / "metrics.jsonl"
    metrics.set_sink(sink)
    tree = tmp_path / "tree"
    _tree(tree, 5)
    database_path = tmp_path / "index.db"

    SearchEngine(SearchDatabase(database_path)).search("bjt")
    assert sink.exists()
    assert metrics.MetricsCollector.load(sink)[-1]["kind"] == "search"

    # The index record must APPEND (force flush) without losing history.
    Indexer(SearchDatabase(database_path)).index_root(tree)
    loaded = metrics.MetricsCollector.load(sink)
    assert [record["kind"] for record in loaded] == ["search", "index"]


def test_load_skips_torn_lines(tmp_path):
    sink = tmp_path / "torn.jsonl"
    sink.write_text(
        '{"kind": "search", "latency_ms": 1.5, "results": 0, "seq": 1}\n'
        "BROKEN{{{ this line survived a crash\n"
        '{"kind": "index", "seq": 2}\n',
        encoding="utf-8",
    )
    loaded = metrics.MetricsCollector.load(sink)
    assert [record["seq"] for record in loaded] == [1, 2]


def test_flush_throttled_unless_forced(tmp_path):
    collector = metrics.MetricsCollector()
    sink = tmp_path / "m.jsonl"
    collector.set_sink(sink)

    collector.record_search(12.5, 3)  # first flush passes the throttle
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 1

    collector.record_search(13.0, 4)  # inside the window: buffered only
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 1
    assert collector.flush() is False

    assert collector.flush(force=True) is True
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 2


def test_broken_sink_never_breaks_recording(tmp_path):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")

    collector = metrics.MetricsCollector()
    collector.set_sink(blocker / "nested" / "metrics.jsonl")
    collector.record_search(1.0, 1)  # must not raise
    assert collector.flush(force=True) is False
    assert len(collector.records()) == 1  # still buffered in memory


def test_service_search_persists_into_app_home(tmp_path):
    paths = AppPaths(home=tmp_path / "home")
    service = SearchService(paths=paths)

    results = service.search("bjt")

    sink = paths.home / "metrics.jsonl"
    assert sink.exists()
    loaded = metrics.MetricsCollector.load(sink)
    assert loaded[-1]["kind"] == "search"
    assert loaded[-1]["results"] == len(results)
