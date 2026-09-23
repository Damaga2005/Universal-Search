"""End-to-end query language against a real index (spec 012).

Proves every supported filter changes the result set correctly, that
operators combine with the documented precedence, that invalid queries
surface as feedback in the CLI and the GUI service, and that hostile
input neither crashes nor corrupts the index.
"""

import os
import sys
from pathlib import Path

import pytest

from universal_search.appconfig import AppPaths
from universal_search.cli import main
from universal_search.gui.services import SearchService
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.query import QueryError

# Fixed mtimes (midday UTC so the local calendar date cannot drift a
# day in typical timezones): 2020-05-05 and 2026-06-15.
MTIME_OLD = 1588680000
MTIME_MID = 1781524800

SPECS = [
    ("lecturas/bjt_amplificador.txt",
     "amplificador bjt con polarizacion estable"),
    ("lecturas/cmos_mux.txt", "mux cmos de dos a uno logica digital"),
    ("informes/informe_final.md", "resumen ejecutivo del informe final"),
    ("datos/tabla.csv", "col1,col2,valor,medida"),
    ("viejo/nota_2020.txt", "nota antigua archivo"),
    ("nuevo/nota_2026.txt", "nota reciente archivo"),
    ("pesado/archivo_grande.txt", "palabra " * 6000),  # 42 KB
]


def build(tmp_path: Path):
    tree = tmp_path / "tree"
    for relative, content in SPECS:
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    os.utime(tree / "viejo/nota_2020.txt", (MTIME_OLD, MTIME_OLD))
    os.utime(tree / "nuevo/nota_2026.txt", (MTIME_MID, MTIME_MID))
    database_path = tmp_path / "index.db"
    Indexer(SearchDatabase(database_path)).index_root(tree)
    return SearchEngine(SearchDatabase(database_path))


def names(results) -> list[str]:
    return sorted(r.name for r in results)


# -- every filter changes the result set --------------------------------------

def test_type_filter_selects_extensions(tmp_path):
    engine = build(tmp_path)
    txt = engine.search("type:txt")
    assert txt and all(r.name.endswith(".txt") for r in txt)
    assert "tabla.csv" not in names(txt)

    assert names(engine.search("type:md")) == ["informe_final.md"]
    assert names(engine.search("type:csv")) == ["tabla.csv"]
    assert engine.search("type:pdf") == []


def test_filter_only_results_score_zero_and_order_by_recency(tmp_path):
    engine = build(tmp_path)
    results = engine.search("type:txt")
    assert all(r.score == 0.0 for r in results)  # no text = no relevance
    assert all(r.snippet is None for r in results)
    # Oldest file last (modified_at DESC), boundary file deterministic.
    assert results[-1].name == "nota_2020.txt"


def test_source_filter(tmp_path):
    engine = build(tmp_path)
    local = engine.search("source:local")
    assert len(local) == len(SPECS)
    assert engine.search("source:onedrive") == []


def test_path_filter(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search("path:informes")) == ["informe_final.md"]
    assert len(engine.search("path:lecturas")) == 2
    assert engine.search("path:inexistente") == []


def test_name_filter(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search("name:informe")) == ["informe_final.md"]
    assert names(engine.search("name:bjt_amplificador")) == [
        "bjt_amplificador.txt"
    ]
    assert engine.search("name:zzz") == []


def test_date_filters(tmp_path):
    engine = build(tmp_path)
    total = len(SPECS)

    after = engine.search("after:2026-01-01")
    assert "nota_2020.txt" not in names(after)
    assert len(after) == total - 1

    before = engine.search("before:2021-01-01")
    assert names(before) == ["nota_2020.txt"]

    # Strict semantics: the boundary day itself is excluded by both.
    boundary = engine.search("after:2026-06-15")
    assert "nota_2026.txt" not in names(boundary)
    assert "nota_2026.txt" in names(engine.search("after:2026-06-14"))
    assert "nota_2026.txt" not in names(engine.search("before:2026-06-15"))

    assert engine.search("after:2030-01-01") == []


def test_size_filter(tmp_path):
    engine = build(tmp_path)
    big = engine.search("size:>10KB")
    assert names(big) == ["archivo_grande.txt"]
    assert engine.search("size:>1MB") == []
    assert "archivo_grande.txt" not in names(engine.search("size:<1000"))


def test_text_and_filter_intersect(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search("bjt type:txt")) == ["bjt_amplificador.txt"]
    assert engine.search("bjt type:md") == []  # conjunction, not union
    assert names(engine.search("nota after:2026-01-01")) == [
        "nota_2026.txt"
    ]


# -- operators: precedence, grouping, phrases, negation -----------------------

def test_or_unions_and_implicit_and_intersects(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search("amplificador OR resumen")) == [
        "bjt_amplificador.txt",
        "informe_final.md",
    ]
    assert engine.search("amplificador resumen") == []  # AND: no overlap


def test_precedence_and_binds_tighter_than_or(tmp_path):
    engine = build(tmp_path)
    # Or(And(amp, resumen), mux): the AND matches nothing, so only mux.
    assert names(engine.search("amplificador resumen OR mux")) == [
        "cmos_mux.txt"
    ]


def test_parentheses_group_or(tmp_path):
    engine = build(tmp_path)
    assert len(engine.search("(amplificador OR resumen)")) == 2
    assert names(engine.search("bjt (amplificador OR zzz)")) == [
        "bjt_amplificador.txt"
    ]


def test_phrase_requires_adjacency_and_order(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search('"amplificador bjt"')) == [
        "bjt_amplificador.txt"
    ]
    # Order matters. The name cannot prove it: unicode61 splits
    # "bjt_amplificador" into two tokens, so both orders are adjacent
    # there. The content can: it has "con polarizacion", never the reverse.
    assert names(engine.search('"con polarizacion"')) == [
        "bjt_amplificador.txt"
    ]
    assert engine.search('"polarizacion con"') == []
    # "bjt" and "polarizacion" are separated by "con": phrase fails,
    # AND succeeds.
    assert engine.search('"bjt polarizacion"') == []
    assert names(engine.search("bjt polarizacion")) == [
        "bjt_amplificador.txt"
    ]


def test_negation_excludes_matches(tmp_path):
    engine = build(tmp_path)
    assert names(engine.search("nota -2020")) == ["nota_2026.txt"]
    assert names(engine.search("nota -name:2020")) == ["nota_2026.txt"]
    assert engine.search("-cmos") == []  # negative-only finds nothing
    assert names(engine.search("mux -cmos")) == []


def test_repeated_terms_behave_like_single_occurrence(tmp_path):
    engine = build(tmp_path)
    once = [r.path for r in engine.search("nota")]
    twice = [r.path for r in engine.search("nota nota NOTA")]
    assert once == twice and once


# -- feedback: CLI and GUI share semantics, never tracebacks ------------------

def test_invalid_query_is_feedback_through_the_service(tmp_path):
    service = SearchService(paths=AppPaths(home=tmp_path / "home"))

    assert service.search("bjt AND") == []
    assert service.last_query_error is not None
    assert "AND" in service.last_query_error

    service.search("type:pdf OR x")  # filters may not sit inside OR
    assert "OR" in (service.last_query_error or "")

    service.search("mux")  # a valid query clears the previous error
    assert service.last_query_error is None


def test_invalid_query_cli_prints_error_not_traceback(tmp_path, capsys):
    monkey_database = tmp_path / "never-opened.db"
    monkeypatch_argv = [
        "universal-search",
        "search",
        "bjt AND",
        "--database",
        str(monkey_database),
    ]
    import unittest.mock as mock

    with mock.patch.object(sys, "argv", monkeypatch_argv):
        # Exit code 1: a malformed query is an error, not a search that
        # happened to return nothing.
        with pytest.raises(SystemExit) as exit_info:
            main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "AND" in captured.err
    assert "Traceback" not in captured.err
    assert not monkey_database.exists()  # failed before touching the db


def test_valid_query_cli_still_prints_results(tmp_path, capsys):
    engine = build(tmp_path)
    del engine  # index built on disk under tmp_path / "index.db"
    argv = [
        "universal-search",
        "search",
        "mux",
        "--database",
        str(tmp_path / "index.db"),
    ]
    import unittest.mock as mock

    with mock.patch.object(sys, "argv", argv):
        main()
    captured = capsys.readouterr()
    assert "cmos_mux.txt" in captured.out
    assert "Traceback" not in captured.err


# -- hostile input cannot corrupt the index -----------------------------------

def test_invalid_query_never_reaches_the_metrics_recorder(tmp_path):
    """Translation fails before any I/O, so only real searches are timed."""
    from universal_search import metrics

    engine = build(tmp_path)
    metrics.reset()
    metrics.set_sink(tmp_path / "metrics.jsonl")
    try:
        with pytest.raises(QueryError):
            engine.search("bjt AND")
        engine.search("mux")
        searches = [r for r in metrics.records() if r["kind"] == "search"]
    finally:
        metrics.set_sink(None)
        metrics.reset()
    assert len(searches) == 1  # the invalid query is not a measurement
    assert searches[0]["results"] == 1


def test_injection_style_queries_never_break_the_index(tmp_path):
    engine = build(tmp_path)
    before = _count(tmp_path / "index.db")

    for raw in (
        "'; DROP TABLE documents; --",
        'bjt" OR "1"="1',
        "1=1 OR 1=1",
        "%00 OR _%",
    ):
        try:
            engine.search(raw)  # valid plans execute harmlessly
        except QueryError:
            pass  # rejected queries are feedback, not crashes

    assert _count(tmp_path / "index.db") == before
    assert len(engine.search("mux")) == 1  # index still answers normally


def _count(database_path: Path) -> int:
    connection = SearchDatabase(database_path).connect()
    try:
        return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        connection.close()
