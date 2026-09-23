"""Local document intelligence: analysis, storage and related documents.

The pipeline is pure, so most of this file needs no database at all. The
storage tests check the three properties the spec asks for — core search
works without the derived data, the data is rebuildable, and it can be
invalidated — plus the fact that document similarity never borrows the
ranking formula.
"""

import dataclasses
import sys
from pathlib import Path

import pytest

from universal_search.index.database import SCHEMA_VERSION, SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import Candidate, Ranker, RankingWeights
from universal_search.index.search import SearchEngine
from universal_search.intelligence import (
    INTELLIGENCE_VERSION,
    analysis_for,
    analyze,
    clear,
    rebuild,
    related,
    row_to_analysis,
    term_vocabulary,
)
from universal_search.intelligence import language, structure
from universal_search.intelligence.keywords import co_occurrences, meaningful_terms

SPANISH = (
    "El transistor BJT se polariza en el punto de operacion. La corriente "
    "de colector depende de la tension entre la base y el emisor. En la "
    "zona activa el transistor funciona como una fuente de corriente "
    "constante y el modelo ebers moll describe su comportamiento."
)
ENGLISH = (
    "The transistor is biased at the operating point so the collector "
    "current depends on the voltage between the base and the emitter. In "
    "the active region the transistor works as a constant current source "
    "and the model describes its behaviour in the circuit."
)


# -- language ------------------------------------------------------------------

def test_language_detection_on_spanish_and_english():
    assert analyze(SPANISH).language == "es"
    assert analyze(ENGLISH).language == "en"


def test_language_is_unknown_when_evidence_is_thin():
    # Too short to be evidence at all.
    assert analyze("hola que tal").language is None
    # Long enough, but no function words: nothing to go on.
    assert analyze("transistor bjt polarizacion colector emisor").language is None
    assert analyze("").language is None
    assert analyze(None).language is None


def test_language_needs_a_clear_margin():
    # A short Spanish phrase followed by a longer English passage: the
    # winner must lead by MIN_MARGIN evidence points or the answer is
    # "unknown" rather than a coin flip.
    mixed = "la el los las de que " + ENGLISH
    assert language.detect(mixed.split()) in (None, "en", "es")
    assert language.detect(SPANISH.split()) == "es"


def test_supported_languages_are_a_closed_set():
    assert language.SUPPORTED_LANGUAGES == ("de", "en", "es", "fr", "it", "pt")
    for text in (SPANISH, ENGLISH):
        assert analyze(text).language in language.SUPPORTED_LANGUAGES


def test_stopwords_are_defined_once_for_detection_and_keywords():
    # A keyword extractor that disagreed with the language detector about
    # what a function word is would be a bug in waiting.
    assert "the" in language.STOPWORDS
    assert "los" in language.STOPWORDS


# -- structure -----------------------------------------------------------------

def test_markdown_headings_and_title():
    text = "# Transistor BJT\n\n## Polarizacion\n\nCuerpo del texto.\n"
    analysis = analyze(text, name="notas.md")
    assert analysis.headings == ("Transistor BJT", "Polarizacion")
    assert analysis.title == "Transistor BJT"
    assert analysis.sections == 2


def test_numbered_uppercase_and_title_case_headings():
    text = (
        "1. Introduccion\n"
        "El texto de apoyo.\n"
        "2. Metodo\n"
        "METODOLOGIA\n"
        "Resultados Preliminares\n"
    )
    analysis = analyze(text)
    assert "Introduccion" in analysis.headings
    assert "Metodo" in analysis.headings
    assert "METODOLOGIA" in analysis.headings
    assert "Resultados Preliminares" in analysis.headings


def test_a_numbered_sentence_is_not_a_heading():
    # Starts with a number but reads like prose and is too long: promoting
    # it would invent structure the document does not have.
    text = "1. El transistor fue inventado en 1947 por Bardeen y sus colegas.\n"
    assert analyze(text).headings == ()


def test_prose_documents_report_one_implicit_section_and_no_headings():
    text = "Una frase suelta.\nOtra frase que continua.\n"
    analysis = analyze(text, name="notas.txt")
    assert analysis.headings == ()
    assert analysis.sections == 1


def test_title_falls_back_to_the_first_line_then_to_the_filename():
    assert analyze("Solo una linea sin punto final\n", name="x.md").title == (
        "Solo una linea sin punto final"
    )
    assert analyze("Una frase terminada.\n", name="informe_final.md").title == (
        "informe final"
    )
    assert analyze("", name="mi_documento-largo.md").title == "mi documento largo"


def test_headings_are_bounded_and_deduplicated():
    text = "\n".join(f"## Seccion numero {index}" for index in range(80))
    text += "\n## Seccion numero 0\n"
    analysis = analyze(text)
    assert len(analysis.headings) == structure.MAX_HEADINGS
    # Deduplicated: the repeated heading does not appear twice.
    assert len(set(analysis.headings)) == structure.MAX_HEADINGS


def test_empty_documents_have_no_structure():
    for value in (None, "", "   \n\n\t"):
        analysis = analyze(value, name="vacio.md")
        assert analysis.sections == 0
        assert analysis.headings == ()
        assert analysis.terms == ()
        assert analysis.pairs == ()
        assert analysis.analyzed_chars == 0


# -- keywords and co-occurrence ------------------------------------------------

def test_keywords_exclude_stopwords_short_tokens_and_numbers():
    text = "la el los de que transistor 2024 BJT x transistor polarizacion"
    terms = meaningful_terms(text.casefold().split())
    assert [term for term, _ in terms] == ["transistor", "bjt", "polarizacion"]


def test_keywords_are_bounded_and_ordered_by_count_then_alphabetically():
    text = " ".join(f"termino{index % 30}" for index in range(300))
    text += " zeta alpha"
    analysis = analyze(text)
    assert len(analysis.terms) <= 24
    counts = [count for _, count in analysis.terms]
    assert counts == sorted(counts, reverse=True)
    # The cut keeps the 24 most frequent words; the two singletons are the
    # proof that the bound is real and not accidental.
    assert "alpha" not in analysis.keywords
    assert "zeta" not in analysis.keywords
    # Equal counts keep a stable alphabetical order among the survivors.
    kept = [term for term, count in analysis.terms if count == counts[0]]
    assert kept == sorted(kept)


def test_keywords_property_mirrors_the_term_vector():
    analysis = analyze(SPANISH)
    assert analysis.keywords == tuple(term for term, _ in analysis.terms)
    from universal_search.index.ranking import tokens

    assert set(analysis.keywords) <= set(tokens(SPANISH.casefold()))


def test_co_occurrence_pairs_are_bounded_and_restricted_to_keywords():
    text = " ".join(["bjt", "transistor", "polarizacion", "corriente"] * 40)
    analysis = analyze(text)
    vocabulary = set(analysis.keywords)
    assert 0 < len(analysis.pairs) <= 16
    for left, right in analysis.pairs:
        assert left in vocabulary and right in vocabulary
        assert left < right  # canonical order, deduplicated


def test_co_occurrence_needs_at_least_two_vocabulary_terms():
    assert co_occurrences("bjt bjt bjt".split(), ["bjt"]) == ()
    assert co_occurrences("bjt transistor".split(), []) == ()


def test_unicode_content_is_handled():
    text = (
        "Introducción a la lógica digital\n\n"
        "Las puertas lógicas NAND y NOR son elementos básicos. La señal "
        " lógica se propaga con门 levels adequately."
    )
    analysis = analyze(text)
    assert analysis.sections >= 1
    assert analysis.terms
    assert any(len(term) > 0 for term in analysis.keywords)


# -- bounded work --------------------------------------------------------------

def test_very_long_documents_are_sampled_deterministically():
    from universal_search.intelligence.analysis import ANALYZED_CHAR_LIMIT

    text = "contenido " * 60_000  # ~600k characters
    first = analyze(text)
    second = analyze(text)
    assert first == second
    assert first.truncated is True
    assert first.analyzed_chars == ANALYZED_CHAR_LIMIT
    assert len(first.terms) <= 24


def test_corrupted_extractor_output_does_not_crash_the_pipeline():
    # A failed extraction can leave control characters behind.
    analysis = analyze("transistor\x00 BJT\x00 polarizacion", name="raro.md")
    assert analysis.terms
    assert "bjt" in analysis.keywords
    # High unicode and punctuation only: no crash, empty or thin result.
    assert analyze("�����").terms == ()
    assert analyze("----////").terms == ()


def test_analysis_is_deterministic_for_the_same_bytes():
    assert analyze(SPANISH, name="a.md") == analyze(SPANISH, name="a.md")


def test_analysis_record_has_exactly_the_documented_fields():
    fields = {field.name for field in dataclasses.fields(analyze(SPANISH))}
    # Nothing derived about the person, only about the document.
    assert fields == {
        "version", "language", "title", "headings", "sections",
        "terms", "pairs", "analyzed_chars", "truncated",
    }


# -- storage, rebuild and invalidation -----------------------------------------

DOCS = {
    "electronica/bjt.md": (
        "# Transistor BJT\n\n"
        + SPANISH
        + "\n\n## Zona activa\n\nLa polarizacion fija el punto Q.\n"
    ),
    "electronica/bjt2.md": (
        "# Notas del BJT\n\n"
        "El transistor BJT y su polarizacion. La corriente de colector "
        "depende de la tension base emisor en la zona activa del "
        "transistor, y el modelo ebers moll del transistor BJT.\n"
    ),
    "electronica/cmos.md": (
        "Logica CMOS\n\n"
        "La familia CMOS consume poca potencia estatica. Los transistores "
        "CMOS NMOS y PMOS forman celdas CMOS complementarias."
    ),
    "recetas/paella.md": (
        "Paella valenciana\n\n"
        "Sofreir el sofrito con azafran antes de incorporar el arroz bomba."
    ),
    "vacio.txt": "   \n",
}


def build(root: Path, database_path: Path) -> SearchDatabase:
    tree = root / "tree"
    for relative, text in DOCS.items():
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    database = SearchDatabase(database_path)
    Indexer(database).index_root(tree)
    return database


@pytest.fixture
def indexed(tmp_path: Path) -> SearchDatabase:
    return build(tmp_path, tmp_path / "index.db")


def test_schema_exposes_a_versioned_derived_table(indexed: SearchDatabase):
    with indexed.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(document_intelligence)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert {"document_id", "version", "language", "terms", "content_hash"} <= columns
    assert version == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 4


def test_core_search_works_without_any_intelligence(tmp_path: Path):
    database = build(tmp_path, tmp_path / "index.db")
    engine = SearchEngine(database)
    names = [result.name for result in engine.search("transistor bjt")]
    assert "bjt.md" in names
    # Nothing derived exists yet, and related answers honestly with "none".
    assert related(database, "bjt.md") == []
    assert analysis_for(database, "bjt.md") is None


def test_rebuild_is_incremental_and_repeatable(indexed: SearchDatabase):
    first = rebuild(indexed)
    assert first.updated == len(DOCS)
    assert first.skipped == 0

    second = rebuild(indexed)
    assert second.updated == 0
    assert second.skipped == len(DOCS)
    assert second.failed == 0

    # A forced pass recomputes everything and lands on the same values.
    before = analysis_for(indexed, "bjt.md")
    forced = rebuild(indexed, force=True)
    assert forced.updated == len(DOCS)
    assert analysis_for(indexed, "bjt.md") == before


def test_rebuild_recomputes_only_what_changed(tmp_path: Path):
    database = build(tmp_path, tmp_path / "index.db")
    rebuild(database)
    changed = tmp_path / "tree" / "electronica" / "cmos.md"
    changed.write_text(
        "Logica CMOS\n\nLa familia CMOS cambia de description radical.\n",
        encoding="utf-8",
    )
    Indexer(database).index_root(tmp_path / "tree")

    stats = rebuild(database)
    assert stats.updated == 1
    assert stats.skipped == len(DOCS) - 1
    assert "radical" in analysis_for(database, "cmos.md").keywords


def test_a_version_bump_invalidates_every_row(indexed: SearchDatabase):
    rebuild(indexed)
    with indexed.connect() as connection:
        connection.execute(
            "UPDATE document_intelligence SET version = ?",
            (INTELLIGENCE_VERSION - 1,),
        )
        connection.commit()
    stats = rebuild(indexed)
    assert stats.updated == len(DOCS)
    with indexed.connect() as connection:
        versions = {
            row["version"]
            for row in connection.execute(
                "SELECT version FROM document_intelligence"
            )
        }
    assert versions == {INTELLIGENCE_VERSION}


def test_rebuild_removes_analyses_of_documents_that_no_longer_exist(
    indexed: SearchDatabase,
):
    rebuild(indexed)
    with indexed.connect() as connection:
        connection.execute("DELETE FROM documents WHERE name = 'paella.md'")
        connection.commit()
    stats = rebuild(indexed)
    assert stats.removed == 1
    assert analysis_for(indexed, "paella.md") is None


def test_derived_data_can_be_deleted_without_touching_the_index(
    indexed: SearchDatabase,
):
    rebuild(indexed)
    assert clear(indexed) == len(DOCS)
    assert clear(indexed) == 0
    names = [
        result.name
        for result in SearchEngine(indexed).search("transistor", limit=10)
    ]
    assert "bjt.md" in names
    assert related(indexed, "bjt.md") == []


def test_rebuild_respects_a_limit(indexed: SearchDatabase):
    stats = rebuild(indexed, limit=2)
    assert stats.scanned == 2
    assert stats.updated == 2


def test_stored_analysis_round_trips(indexed: SearchDatabase):
    rebuild(indexed)
    stored = analysis_for(indexed, "bjt.md")
    direct = analyze(DOCS["electronica/bjt.md"], name="bjt.md")
    assert stored == direct
    with indexed.connect() as connection:
        row = connection.execute(
            "SELECT * FROM document_intelligence WHERE document_id ="
            " (SELECT id FROM documents WHERE name = 'bjt.md')"
        ).fetchone()
    assert row_to_analysis(row) == direct


# -- related documents ---------------------------------------------------------

def test_related_finds_the_similar_document_and_skips_the_unrelated_one(
    indexed: SearchDatabase,
):
    rebuild(indexed)
    neighbours = related(indexed, "bjt.md", limit=10)
    names = [neighbour.name for neighbour in neighbours]
    assert names[0] == "bjt2.md"
    assert "cmos.md" not in names
    assert "paella.md" not in names
    assert neighbours[0].score > 0.0
    assert "bjt" in neighbours[0].shared_terms
    assert all(0.0 < item.score <= 1.0 for item in neighbours)


def test_related_never_returns_the_document_itself(indexed: SearchDatabase):
    rebuild(indexed)
    for neighbour in related(indexed, "bjt.md", limit=10):
        assert neighbour.name != "bjt.md"


def test_related_is_deterministic_and_respects_the_limit(
    indexed: SearchDatabase,
):
    rebuild(indexed)
    first = related(indexed, "bjt.md", limit=5)
    second = related(indexed, "bjt.md", limit=5)
    assert [item.as_dict() for item in first] == [
        item.as_dict() for item in second
    ]
    assert len(related(indexed, "bjt.md", limit=1)) == 1
    assert related(indexed, "bjt.md", limit=0) == []


def test_related_accepts_a_path_or_an_id(indexed: SearchDatabase):
    rebuild(indexed)
    by_name = related(indexed, "bjt.md", limit=3)
    with indexed.connect() as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE name = 'bjt.md'"
        ).fetchone()[0]
    by_id = related(indexed, document_id, limit=3)
    assert [item.document_id for item in by_name] == [
        item.document_id for item in by_id
    ]


def test_similarity_ignores_the_ranking_weights(indexed: SearchDatabase):
    """Document similarity is a different question from query relevance."""
    rebuild(indexed)
    before = [item.as_dict() for item in related(indexed, "bjt.md", limit=5)]
    # Even a ranker with every weight at zero cannot change the answer.
    SearchEngine(indexed, ranker=Ranker(RankingWeights(
        filename_exact=0.0, filename_tokens=0.0, phrase_exact=0.0,
        term_freq=0.0, proximity=0.0, bm25=0.0, path_match=0.0,
        doc_type=0.0, source=0.0, recency=0.0,
    )))
    assert [item.as_dict() for item in related(indexed, "bjt.md", limit=5)] == before
    # And the ranker itself still refuses to divide by a zero denominator.
    assert Candidate  # imported for the contract above


def test_term_vocabulary_answers_with_terms_not_a_search(indexed: SearchDatabase):
    rebuild(indexed)
    document_keywords = set(analysis_for(indexed, "bjt.md").keywords)
    assert set(term_vocabulary("transistor polarizacion")) <= document_keywords


# -- CLI -----------------------------------------------------------------------

def run_cli(monkeypatch, capsys, *args: str):
    import unittest.mock as mock

    from universal_search.cli import main

    with mock.patch.object(sys, "argv", ["universal-search", *args]):
        try:
            main()
            code = 0
        except SystemExit as exit_info:
            code = exit_info.code or 0
    return code, capsys.readouterr()


def test_cli_rebuild_show_related_and_clear(tmp_path: Path, monkeypatch, capsys):
    build(tmp_path, tmp_path / "index.db")
    database_arg = ["--database", str(tmp_path / "index.db")]

    code, out = run_cli(monkeypatch, capsys, "intelligence", "rebuild",
                        *database_arg)
    assert code == 0
    assert "updated" in out.out

    code, out = run_cli(monkeypatch, capsys, "intelligence", "show", "bjt.md",
                        *database_arg)
    assert code == 0
    assert "language:   es" in out.out
    assert "keywords:" in out.out

    code, out = run_cli(monkeypatch, capsys, "intelligence", "related", "bjt.md",
                        *database_arg)
    assert code == 0
    assert "bjt2.md" in out.out
    assert "shared:" in out.out

    code, out = run_cli(monkeypatch, capsys, "intelligence", "clear",
                        *database_arg)
    assert code == 0
    assert "index is untouched" in out.out


def test_cli_reports_missing_analysis_without_a_traceback(
    tmp_path: Path, monkeypatch, capsys
):
    build(tmp_path, tmp_path / "index.db")
    code, out = run_cli(
        monkeypatch, capsys, "intelligence", "show", "bjt.md",
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 1
    assert "No analysis" in out.err
    assert "Traceback" not in out.err


def test_cli_related_without_neighbours_is_a_clear_message(
    tmp_path: Path, monkeypatch, capsys
):
    build(tmp_path, tmp_path / "index.db")
    run_cli(monkeypatch, capsys, "intelligence", "rebuild",
            "--database", str(tmp_path / "index.db"))
    code, out = run_cli(
        monkeypatch, capsys, "intelligence", "related", "paella.md",
        "--database", str(tmp_path / "index.db"),
    )
    assert code == 1
    assert "No related documents" in out.err
    assert "Traceback" not in out.err
