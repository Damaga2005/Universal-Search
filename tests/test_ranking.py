from datetime import datetime, timezone
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import (
    DEFAULT_WEIGHTS,
    Candidate,
    Ranker,
    query_terms,
)
from universal_search.index.search import SearchEngine, build_snippet

from docfactories import make_pdf


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def build_index(root: Path, db_path: Path) -> SearchDatabase:
    database = SearchDatabase(db_path)
    Indexer(database).index_root(root)
    return database


def names(results) -> list[str]:
    return [result.name for result in results]


# -- the acceptance scenario: query "BJT" ------------------------------------

def build_engineering_tree(root: Path) -> None:
    write(
        root / "BJT_Ebers_Moll.pdf",
        make_pdf("BJT Ebers-Moll: operacion de transistores BJT"),
    )
    write(
        root / "Tema_6.pdf",
        make_pdf(
            "Amplificadores BJT de punto fijo. BJT BJT BJT BJT BJT "
            "BJT BJT BJT. Modelo Ebers-Moll del BJT."
        ),
    )
    write(
        root / "tema.md",
        "Notas de clase sobre BJT y punto de operacion. BJT BJT.",
    )
    write(root / "carpeta-bjt" / "otro.txt", "ondas y mecanica clasica")


def test_bjt_query_ranks_filename_then_content_then_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "files"
    build_engineering_tree(root)
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("BJT")

    assert names(results) == [
        "BJT_Ebers_Moll.pdf",
        "Tema_6.pdf",
        "tema.md",
        "otro.txt",
    ]
    scores = [result.score for result in results]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_path_only_document_never_outranks_content(tmp_path: Path) -> None:
    root = tmp_path / "files"
    build_engineering_tree(root)
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("BJT")
    by_name = {result.name: result.score for result in results}

    # Content documents clearly beat the document whose path matches only.
    assert by_name["tema.md"] > by_name["otro.txt"] + 0.15


# -- filename signals ---------------------------------------------------------

def test_exact_filename_match_outranks_weak_content_match(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "BJT.md", "archivo de referencia")
    write(root / "debil.md", "una unica mencion de BJT en el documento")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("BJT")

    assert names(results) == ["BJT.md", "debil.md"]


def test_filename_tokens_contribute(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "ebers_moll_teoria.md", "teoria general del transistor")
    write(root / "otro.md", "transistor teoria general")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("ebers moll")

    assert names(results)[0] == "ebers_moll_teoria.md"


# -- phrase and proximity -----------------------------------------------------

def test_scattered_terms_outranked_by_exact_phrase(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "cerca.md", "transistor polar de punto fijo bien explicado")
    write(
        root / "lejos.md",
        "el transistor fue invento hace tiempo y luego llego el metodo "
        "polar directo de uso raro con palabras de relleno por todos lados "
        "hasta separar los dos terminos buscados",
    )
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("transistor polar")

    assert names(results) == ["cerca.md", "lejos.md"]
    nearby = results[0]
    distant = results[1]
    # Adjacency wins the phrase signal outright.
    assert nearby.score > distant.score + 0.1


# -- determinism ---------------------------------------------------------------

def test_results_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "files"
    build_engineering_tree(root)
    write(root / "varios.md", "comentarios sueltos sobre amplificadores")
    database = build_index(root, tmp_path / "index" / "search.db")
    engine = SearchEngine(database)

    first = [(r.name, r.path, r.score) for r in engine.search("BJT")]
    second = [(r.name, r.path, r.score) for r in engine.search("BJT")]

    assert first == second


def test_equal_scores_break_ties_by_path(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "aaa" / "mismo.md", "contenido identico compartido")
    write(root / "zzz" / "mismo.md", "contenido identico compartido")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("identico")

    assert names(results) == ["mismo.md", "mismo.md"]
    assert results[0].path < results[1].path
    assert results[0].score == results[1].score


# -- recency -------------------------------------------------------------------

def test_recency_is_bounded_secondary_signal(tmp_path: Path) -> None:
    root = tmp_path / "files"
    old = write(root / "viejo" / "informe.md", "informe final del proyecto")
    write(root / "nuevo" / "informe.md", "informe final del proyecto")
    old_time = datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp()
    import os

    os.utime(old, (old_time, old_time))
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("informe")

    assert len(results) == 2
    newer, older = results
    assert newer.path != older.path
    # Same score everywhere except recency: newer wins, by a bounded margin.
    difference = newer.score - older.score
    assert difference > 0
    assert difference < 0.015  # the full weight is ~0.0107


# -- special queries ------------------------------------------------------------

def test_c_style_query_ranks_content_over_drive_letter(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "compilador.md", "notas del compilador C++ con plantillas")
    write(root / "c" / "carpeta.txt", "archivo suelto sin relacion")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("C++")

    assert results, "query must match something"
    assert results[0].name == "compilador.md"
    # Single-character terms are excluded from the path signal entirely.
    ranker = Ranker()
    drive_candidate = Candidate(
        name="carpeta.txt",
        path="C:/carpeta/carpeta.txt",
        content="archivo suelto sin relacion",
        source="local",
        modified_at=None,
        bm25_rank=-1.0,
    )
    assert ranker.signals(drive_candidate, query_terms("C++"))["path_match"] == 0.0


def test_column_syntax_query_falls_back_and_ranks(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "nota.md", "foo bar juntos en la nota")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("foo:bar")

    assert names(results) == ["nota.md"]


def test_quoted_phrase_query_survives_ranking(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "cerca.md", "transistor polar de punto fijo")
    write(root / "lejos.md", "polar y transistor separados lejos")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search('"transistor polar"')

    assert names(results) == ["cerca.md"]


# -- unit tests of individual signals -------------------------------------------

def make_candidate(**overrides) -> Candidate:
    values = {
        "name": "informe.md",
        "path": "C:/docs/proyecto/informe.md",
        "content": "contenido de ejemplo",
        "source": "local",
        "modified_at": None,
        "bm25_rank": -4.0,
    }
    values.update(overrides)
    return Candidate(**values)


def test_signal_filename_exact() -> None:
    ranker = Ranker()
    signals = ranker.signals(make_candidate(name="BJT.md"), query_terms("BJT"))
    assert signals["filename_exact"] == 1.0
    signals = ranker.signals(make_candidate(name="otro.md"), query_terms("BJT"))
    assert signals["filename_exact"] == 0.0
    # full name with extension also counts
    signals = ranker.signals(make_candidate(name="bola.md"), query_terms("bola.md"))
    assert signals["filename_exact"] == 1.0


def test_signal_filename_tokens_fraction() -> None:
    ranker = Ranker()
    signals = ranker.signals(
        make_candidate(name="apuntes_ebers.md"), query_terms("apuntes ebers transistor")
    )
    assert signals["filename_tokens"] == 2 / 3


def test_signal_bm25_normalization() -> None:
    ranker = Ranker()
    signals = ranker.signals(make_candidate(bm25_rank=-4.0), query_terms("x"))
    assert signals["bm25"] == 0.8


def test_signal_doc_type() -> None:
    ranker = Ranker()
    assert ranker.signals(make_candidate(content="hay texto"), query_terms("x"))["doc_type"] == 1.0
    assert ranker.signals(make_candidate(content=None), query_terms("x"))["doc_type"] == 0.4


def test_signal_recency_neutral_when_unknown() -> None:
    ranker = Ranker()
    assert ranker.signals(make_candidate(), query_terms("x"))["recency"] == 0.75


def test_signal_recency_recent_is_higher() -> None:
    ranker = Ranker()
    recent = make_candidate(modified_at="2026-09-20T00:00:00+00:00")
    ancient = make_candidate(modified_at="2015-01-01T00:00:00+00:00")
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    recent_value = ranker.signals(recent, query_terms("x"), now=now)["recency"]
    ancient_value = ranker.signals(ancient, query_terms("x"), now=now)["recency"]
    assert recent_value > ancient_value
    assert ancient_value >= 0.5


def test_usage_signal_is_reserved_and_inert() -> None:
    ranker = Ranker()
    candidate = make_candidate()
    base = ranker.score(candidate, query_terms("contenido"))
    boosted = ranker.score(candidate, query_terms("contenido"), usage_boost=1.0)
    assert base == boosted  # weight is 0.0: no personalization yet
    assert DEFAULT_WEIGHTS.usage == 0.0


def test_scores_are_normalized() -> None:
    ranker = Ranker()
    perfect = make_candidate(
        name="contenido.md",
        path="C:/contenido/contenido.md",
        content="contenido " * 30,
        modified_at="2026-09-22T00:00:00+00:00",
        bm25_rank=-40.0,
    )
    score = ranker.score(perfect, query_terms("contenido"))
    assert 0.0 <= score <= 1.0
    assert score > 0.8


# -- snippets --------------------------------------------------------------------

def test_path_only_match_does_not_dump_full_content(tmp_path: Path) -> None:
    root = tmp_path / "files"
    long_text = " ".join(f"palabra{i}" for i in range(200))
    write(root / "sub" / "physics.txt", long_text)
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("physics")

    assert names(results) == ["physics.txt"]
    # No term inside the content: there is nothing useful to show.
    assert results[0].snippet is None


def test_content_match_snippet_is_highlighted(tmp_path: Path) -> None:
    root = tmp_path / "files"
    write(root / "nota.md", "el condensador se descarga por la resistencia")
    database = build_index(root, tmp_path / "index" / "search.db")

    results = SearchEngine(database).search("condensador")

    assert results[0].snippet is not None
    assert "condensador" in results[0].snippet


def test_build_snippet_windows_around_first_term() -> None:
    content = "inicio " * 50 + "aguja en el pajar " + "fin " * 50
    snippet = build_snippet(content, ("aguja",))
    assert snippet is not None
    assert "aguja" in snippet
    assert len(snippet) < len(content)


def test_build_snippet_without_terms_or_content() -> None:
    assert build_snippet(None, ("x",)) is None
    assert build_snippet("contenido", ()) is None
    assert build_snippet("contenido", ("ausente",)) is None
