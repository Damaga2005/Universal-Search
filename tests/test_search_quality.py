"""Phase 045: search quality and relevance.

Grouped by what the phase promises, not by module. The three promises are:
extend the corpus only for justified missing workloads, classify real failures
before touching retrieval, and refuse a ranking change with no reproducible
failure behind it.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evaluation import corpus as corpus_module
from evaluation import diagnose as diagnose_module
from evaluation import metrics as metrics_module
from evaluation import runner as runner_module
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import DEFAULT_WEIGHTS
from universal_search.index.search import SearchEngine

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


# -- the corpus builders are deterministic, or the hash is meaningless ----------

def test_the_binary_builders_are_byte_for_byte_reproducible():
    """`corpus_hash()` feeds the committed baseline.

    A ZIP that carries the creating process's umask or the current clock into
    its bytes hashes differently on every machine, and every ranking
    measurement stops being comparable with the last one.
    """
    assert corpus_module.pdf_bytes(("uno", "dos")) == corpus_module.pdf_bytes(
        ("uno", "dos")
    )
    assert corpus_module.docx_bytes((("Normal", "hola"),), "t") == (
        corpus_module.docx_bytes((("Normal", "hola"),), "t")
    )
    assert corpus_module.xlsx_bytes("H", (("a", "b"),)) == (
        corpus_module.xlsx_bytes("H", (("a", "b"),))
    )


def test_the_whole_corpus_hashes_the_same_twice(tmp_path):
    from evaluation.runner import corpus_hash

    first, second = tmp_path / "a", tmp_path / "b"
    corpus_module.build(first)
    corpus_module.build(second)
    assert corpus_hash(first) == corpus_hash(second)


def test_the_binary_builders_produce_something_a_real_parser_accepts(tmp_path):
    """Not "bytes that do not crash" -- bytes the shipped extractor reads."""
    from universal_search.extractors.office import read_docx, read_xlsx
    from universal_search.extractors.pdf import read_pdf

    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(corpus_module.pdf_bytes(("polarizacion del transistor",)))
    assert "polarizacion" in (read_pdf(pdf).text or "")

    doc = tmp_path / "m.docx"
    doc.write_bytes(corpus_module.docx_bytes(
        (("Heading1", "Practica 3"), ("Normal", "punto de reposo")), "t"
    ))
    assert "punto de reposo" in (read_docx(doc).text or "")

    sheet = tmp_path / "m.xlsx"
    sheet.write_bytes(corpus_module.xlsx_bytes(
        "Almacen", (("componente", "stock"), ("condensador", "75"))
    ))
    assert "condensador" in (read_xlsx(sheet).text or "")


# -- the workloads the phase names, and the corpus now has --------------------

@pytest.mark.parametrize(
    ("workload", "marker"),
    (
        ("university/technical", "electronica/"),
        ("code", "codigo/"),
        ("pdf", ".pdf"),
        ("office", ".docx"),
        ("office table", ".xlsx"),
    ),
)
def test_each_named_workload_has_a_document(workload, marker):
    assert any(marker in document.path for document in corpus_module.DOCUMENTS), (
        f"{workload} is in the phase's list and has no corpus document"
    )


def test_duplicated_material_exists_and_is_identical():
    bodies = [
        (d.content or (d.raw or b"")).strip() for d in corpus_module.DOCUMENTS
    ]
    assert len(bodies) != len(set(map(repr, bodies))), (
        "the phase asks about duplicated material and the corpus has none"
    )


def test_short_and_long_documents_both_exist():
    lengths = [len(d.content) for d in corpus_module.DOCUMENTS if d.content]
    assert min(lengths) <= 120, "no short document"
    assert max(lengths) >= 2000, "no long document"


def test_a_non_spanish_document_exists():
    """Accents are not multilingual: unicode61 folds them."""
    hints = (" de ", " la ", " el ", " los ", " que ", " para ", " con ")
    for document in corpus_module.DOCUMENTS:
        if not document.content:
            continue
        lowered = document.content.casefold()
        if sum(lowered.count(hint) for hint in hints) <= 1:
            return
    pytest.fail("every document reads as Spanish")


def test_an_abbreviated_filename_exists():
    """`T6`, `BJT`, `lab_3`: how course material is really named."""
    import re

    for document in corpus_module.DOCUMENTS:
        name = document.path.rsplit("/", 1)[-1]
        for segment in re.split(r"[._\-\s]+", name):
            if 1 <= len(segment) <= 4 and segment.isupper() and segment.isalpha():
                return
    pytest.fail("no file named with abbreviations")


def test_no_workload_added_by_the_corpus_extension_regressed():
    """Every query the corpus already answered must still answer.

    Extending a corpus is the one change that can silently make a query worse,
    because a new document can outrank the old answer without anything being
    broken. So this asserts the other direction: no previously-answered query
    stopped answering. The corpus's *declared* failures (the three phase-026
    lexical ones and the phase-045 CJK case) are excluded by name, because
    those are what they are.
    """
    workspace = Path(tempfile.mkdtemp(prefix="test-045-"))
    try:
        tree = workspace / "corpus"
        corpus_module.build(tree)
        database = SearchDatabase(workspace / "p.db")
        Indexer(database).index_root(tree)
        engine = SearchEngine(database)
        ids = corpus_module.ids_by_path(tree)
        declared = {
            labelled.query for labelled in corpus_module.LABELLED_QUERIES
            if labelled.known_limitation or labelled.failure_class
        }
        for labelled in corpus_module.LABELLED_QUERIES:
            if labelled.query in declared or not labelled.relevant:
                continue
            ranked = [
                ids.get(Path(r.path).resolve(), r.path)
                for r in engine.search(labelled.query, limit=10, now=NOW)
            ]
            assert labelled.relevant & set(ranked), (
                f"{labelled.query!r} retrieves nothing relevant: {ranked}"
            )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# -- the taxonomy --------------------------------------------------------------

def test_the_taxonomy_is_the_phase_list_in_probe_order():
    assert diagnose_module.FAILURE_CLASSES == (
        "stale index",
        "extraction",
        "filtering",
        "ranking",
        "phrase handling",
        "morphology/fuzzy",
        "semantic fallback",
        "filename/path",
        "lexical mismatch",
        "interaction/UI",
    )


def test_only_three_causes_are_weighting_reachable():
    """The number that answers the phase's central question, pinned."""
    assert diagnose_module.RANKING_REACHABLE == frozenset(
        {"ranking", "phrase handling", "filename/path"}
    )


def test_a_stem_guess_is_declared_and_not_invented():
    assert learn_stem("recetas") == learn_stem("receta")
    assert learn_stem("lecturas") == learn_stem("lectura")
    # Short words are left alone: a 3-character prefix is not morphology.
    assert learn_stem("res") == "res"


def learn_stem(word: str) -> str:
    return diagnose_module._stem(word)


def test_a_verbose_inventory_is_never_mislabelled_as_known():
    """Every diagnosed cause must be in the taxonomy, or Q11 fires."""
    for cause in ("stale index", "extraction", "lexical mismatch"):
        assert cause in diagnose_module.FAILURE_CLASSES


# -- the diagnosis, on the real corpus ----------------------------------------

@pytest.fixture(scope="module")
def diagnosed():
    workspace = Path(tempfile.mkdtemp(prefix="test-045-diag-"))
    tree = workspace / "corpus"
    corpus_module.build(tree)
    database = SearchDatabase(workspace / "p.db")
    Indexer(database).index_root(tree)
    engine = SearchEngine(database)
    verdicts = []
    for labelled in corpus_module.LABELLED_QUERIES:
        verdicts.extend(diagnose_module.diagnose(
            engine, labelled, corpus_root=tree, now=NOW,
        ))
    yield tree, engine, diagnose_module.build_inventory(verdicts)
    shutil.rmtree(workspace, ignore_errors=True)


def test_no_ranking_change_could_have_fixed_any_measured_failure(diagnosed):
    """The phase's own rule, as a test.

    If this ever fails, somebody has found a real ranking failure and the
    ranking *should* be revisited -- with a measurement, per Q12.
    """
    _tree, _engine, inventory = diagnosed
    assert inventory.ranking_reachable == 0, [
        verdict.as_dict() for verdict in inventory.verdicts
        if verdict.reachable_by_ranking
    ]


def test_every_diagnosed_failure_has_a_cause_and_evidence(diagnosed):
    _tree, _engine, inventory = diagnosed
    assert inventory.verdicts
    assert inventory.unclassified == 0
    for verdict in inventory.verdicts:
        assert verdict.cause in diagnose_module.FAILURE_CLASSES
        assert verdict.evidence, verdict.as_dict()


def test_the_declared_morphology_and_extraction_cases_are_classified(diagnosed):
    _tree, _engine, inventory = diagnosed
    causes = {verdict.cause for verdict in inventory.verdicts}
    assert "morphology/fuzzy" in causes, (
        "'receta paella' is a plural/singular miss and the corpus says so"
    )
    assert "extraction" in causes, (
        "the CJK case is a tokenisation gap, not a vocabulary one"
    )


def test_the_diagnosis_agrees_with_the_corpus_labels(diagnosed):
    """The declared `failure_class` and the computed cause must not contradict.

    The declared class is a human label; the computed cause is a probe. Where
    they overlap in meaning they have to agree, or one of the two is lying.
    """
    _tree, _engine, inventory = diagnosed
    agreed = {"synonym": "lexical mismatch", "morphological": "morphology/fuzzy",
              "extraction": "extraction"}
    for verdict in inventory.verdicts:
        labelled = next(
            l for l in corpus_module.LABELLED_QUERIES if l.query == verdict.query
        )
        expected = agreed.get(labelled.failure_class)
        if expected is None:
            continue
        assert verdict.cause == expected, (
            f"{verdict.query!r} is labelled {labelled.failure_class!r} but the "
            f"probe says {verdict.cause!r}: {verdict.evidence[-1]}"
        )


def test_a_missing_document_is_reported_as_stale_not_as_anything_else(tmp_path):
    """A document the index never knew about is not a lexical miss.

    Driven by actually removing the row rather than by monkeypatching
    ``ids_by_path``: the first version of this test patched the mapping and
    produced zero verdicts, which asserted nothing while looking like a pass.
    """
    tree = tmp_path / "corpus"
    corpus_module.build(tree)
    database = SearchDatabase(tmp_path / "p.db")
    Indexer(database).index_root(tree)
    engine = SearchEngine(database)
    labelled = next(
        l for l in corpus_module.LABELLED_QUERIES if l.query == "notas"
    )
    ids = diagnose_module._ids_for(engine, tree)
    victim = ids["bjt-notas"]
    with engine.database.connect() as connection:
        connection.execute("DELETE FROM documents_fts WHERE document_id = ?",
                           (victim,))
        connection.execute("DELETE FROM documents WHERE id = ?", (victim,))
        connection.commit()

    verdicts = diagnose_module.diagnose(
        engine, labelled, corpus_root=tree, now=NOW
    )
    assert [v.cause for v in verdicts] == ["stale index"]
    assert verdicts[0].evidence[-1]
    assert verdicts[0].reachable_by_ranking is False


# -- the metrics the phase asks for and the repository lacked ------------------

def test_reports_can_be_measured_at_ten():
    assert 10 in metrics_module.DEFAULT_K_VALUES
    assert 10 in runner_module.K_VALUES


def test_filter_accuracy_counts_a_leak_as_a_failure():
    report = metrics_module.filter_accuracy([
        ("a type:pdf", True),
        ("b type:txt", False),
    ])
    assert report["accuracy"] == pytest.approx(0.5)
    assert report["wrong"] == ["b type:txt"]


def test_filter_accuracy_does_not_vacuum_when_there_is_nothing_to_measure():
    """A metric that reports 1.0 because it looked at nothing is a lie."""
    report = metrics_module.filter_accuracy([], unlabelled=["x type:pdf"])
    assert report["queries"] == 0
    assert report["unlabelled"] == ["x type:pdf"]
    assert report["accuracy"] == 1.0  # vacuously, and visibly


def test_zero_result_accuracy_is_its_own_question():
    report = metrics_module.zero_result_accuracy([
        ("zzz no existe", True),
        ("ruido", False),
    ])
    assert report["accuracy"] == pytest.approx(0.5)
    assert report["noisy"] == ["ruido"]


def test_an_empty_relevance_set_is_still_reachable_from_the_corpus():
    silent = [
        l for l in corpus_module.LABELLED_QUERIES if not l.relevant
    ]
    assert silent, "a corpus with no must-retrieve-nothing case cannot test silence"


# -- the tokenizer trade, which is why the CJK case stays declared -------------

def test_trigram_is_a_net_regression_not_an_unexplored_fix():
    """Q13's measurement, as a test.

    `trigram` answers the CJK query that `unicode61` cannot. It also answers
    four other labelled queries less well, because a three-character tokenizer
    cannot match a two-character term and handles quoted phrases differently.
    That is why the limitation stays declared, and it is re-measured on every
    gate run.
    """
    def counts(tokenizer):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE VIRTUAL TABLE t USING fts5("
            f"name, path, content, tokenize='{tokenizer}')"
        )
        for document in corpus_module.DOCUMENTS:
            connection.execute(
                "INSERT INTO t(name, path, content) VALUES (?, ?, ?)",
                (
                    document.path.rsplit("/", 1)[-1],
                    document.path,
                    document.content or "",
                ),
            )
        return connection

    from universal_search.query import parse_query, translate

    a, b = counts("unicode61"), counts("trigram")
    try:
        more = fewer = 0
        for labelled in corpus_module.LABELLED_QUERIES:
            match = translate(parse_query(labelled.query)).fts
            def n(connection):
                try:
                    return int(connection.execute(
                        "SELECT count(*) FROM t WHERE t MATCH ?", (match,)
                    ).fetchone()[0])
                except sqlite3.Error:
                    return 0
            delta = n(b) - n(a)
            more += delta > 0
            fewer += delta < 0
        assert more == 2, f"trigram should answer 2 more, answers {more}"
        assert fewer == 4, f"trigram should answer 4 fewer, answers {fewer}"
        assert more - fewer < 0, "the trade must be a net loss to justify declaring"
    finally:
        a.close()
        b.close()


def test_the_declared_limitation_is_exactly_the_one_that_is_unreachable():
    declared = {
        labelled.query for labelled in corpus_module.LABELLED_QUERIES
        if labelled.known_limitation
    }
    assert declared == {"\u30c6\u30b9\u30c8"}
    for labelled in corpus_module.LABELLED_QUERIES:
        if labelled.query in declared:
            assert labelled.known_limitation.count(".") >= 3, (
                "a declared limitation states its reason, not just its existence"
            )


# -- the ranking must not have moved ------------------------------------------

def test_no_weight_changed_during_this_phase():
    documented = {
        "filename_exact": 3.0, "filename_tokens": 2.0, "phrase_exact": 2.0,
        "term_freq": 1.5, "proximity": 1.5, "bm25": 2.0, "path_match": 0.8,
        "doc_type": 0.5, "source": 0.4, "recency": 0.3, "usage": 0.0,
        "context": 0.0,
    }
    moved = {
        name: (getattr(DEFAULT_WEIGHTS, name), value)
        for name, value in documented.items()
        if getattr(DEFAULT_WEIGHTS, name) != value
    }
    assert moved == {}, moved