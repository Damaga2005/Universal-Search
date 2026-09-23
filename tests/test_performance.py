"""Phase 009: interactive queries execute against the prepared index only.

The spec target is explicit — no filesystem scans during a query — and the
latency budgets are regression tripwires: they are intentionally generous
versus the measured baseline so only a real regression trips them.
"""

import os
import time
from pathlib import Path

import pytest

from universal_search.appconfig import AppPaths
from universal_search.domain.document import Document, SourceKind, document_id_for
from universal_search.gui.services import SearchService
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer

DOCUMENT_COUNT = 600
TOPICS = ("termo", "fourier", "laplace", "amplificador", "memoria", "micro")
QUERIES = (
    "termo",
    "fourier",
    "documento aplicado",
    "aplicado tema",
    "conceptos fórmulas",
    "amplificador",
    "memoria",
    "termo aplicado",
)


@pytest.fixture(scope="module")
def populated_home(tmp_path_factory) -> AppPaths:
    """A realistic index (600 small documents), built once for this module."""
    root = tmp_path_factory.mktemp("perf")
    paths = AppPaths(root / "home")
    paths.ensure()
    database = SearchDatabase(paths.database)
    indexer = Indexer(database)
    for number in range(DOCUMENT_COUNT):
        topic = TOPICS[number % len(TOPICS)]
        path = Path("C:/indice") / f"{topic}-{number:04d}.md"
        content = (
            f"{topic} aplicado documento número {number}. "
            "conceptos y fórmulas del tema. "
        )
        document = Document(
            id=document_id_for(SourceKind.LOCAL, path),
            source=SourceKind.LOCAL,
            path=path,
            name=path.name,
            extension=path.suffix.lower(),
            size=len(content.encode("utf-8")),
            created_at=None,
            modified_at=None,
            content=content,
            content_hash=f"{number:x}",
        )
        indexer.upsert(document)
    return paths


def test_query_never_touches_the_filesystem(
    populated_home, monkeypatch
) -> None:
    """AC (spec 009): the UI must not scan the filesystem during a query."""
    service = SearchService(paths=populated_home)

    def exploded(*_args, **_kwargs):
        raise AssertionError("filesystem access during an interactive query")

    monkeypatch.setattr(os, "scandir", exploded)
    monkeypatch.setattr(os, "listdir", exploded)
    monkeypatch.setattr(Path, "rglob", exploded)
    monkeypatch.setattr(Path, "glob", exploded)
    monkeypatch.setattr(Path, "iterdir", exploded)
    monkeypatch.setattr(Path, "resolve", exploded)

    results = service.search("termo", limit=10)
    assert results, "queries still execute against the index"
    # filtered queries too
    assert service.search("termo", limit=10, source="local", doc_type="md")


def test_query_latency_budget(populated_home) -> None:
    """Measured baseline stays far below the budget; regression tripwire."""
    service = SearchService(paths=populated_home)

    for query in QUERIES:  # warm-up (connection + page cache)
        assert service.search(query, limit=50)

    samples: list[float] = []
    for _ in range(3):
        for query in QUERIES:
            started = time.perf_counter()
            results = service.search(query, limit=50)
            samples.append(time.perf_counter() - started)
            assert results
    mean = sum(samples) / len(samples)
    worst = max(samples)
    # Measured 2026-09 after the optimization pass: ~30 ms mean / ~60 ms
    # worst on this fixture (bench009 on 2000 docs: mean 34.4, p95 46.6).
    # Budgets stay ~3x the measurement so only a real regression trips
    # them (pre-optimization mean was 48.6 ms; build was 16x slower).
    assert mean < 0.1, f"mean query latency {mean * 1000:.1f} ms"
    assert worst < 0.3, f"worst query latency {worst * 1000:.1f} ms"


def test_service_startup_and_first_query_budget(tmp_path) -> None:
    """Cold start: paths + schema + first query against an empty index."""
    paths = AppPaths(tmp_path / "home")
    started = time.perf_counter()
    service = SearchService(paths=paths)
    service.search("arranque")  # first query pays connection + SCHEMA
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"startup + first query took {elapsed * 1000:.0f} ms"
    assert service.database.path.parent == paths.home
