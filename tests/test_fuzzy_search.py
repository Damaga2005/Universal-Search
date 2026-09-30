"""Phase 031: robust search for typos and partial words.

The tests are organised around the phase's promises, and around the property
that makes the layer safe: **the blocking index proposes and the verifier
disposes**. A test that only proved "the typo is found" would pass on a layer
that also invents matches, so there is a whole group proving the negative case.
"""

from pathlib import Path

import pytest

from universal_search import fuzzy
from universal_search.fuzzy import (
    FUZZY_VERSION,
    FuzzyIndex,
    FuzzySearchEngine,
    damerau_levenshtein,
    edit_budget,
    fold,
    is_nonsense,
    query_trigrams,
    resolve_query,
    resolve_token,
    selective_trigrams,
    trigram_counts,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


# -- a corpus with the failure classes the phase promises ---------------------

DOCUMENTS = {
    "electronica/bjt-modelo.md": (
        "Modelo Ebers-Moll del transistor BJT. Corriente de colector, "
        "tension de polarizacion y punto de trabajo del transistor."
    ),
    "electronica/notas.txt": (
        "Notas de clase: el amplificador de tension usa un transistor BJT "
        "configurado en emisor comun y la polarizacion fija el punto Q."
    ),
    "electronica/guia.pdf": (
        "Guia rapida del transistor MOSFET. Umbral de puerta, corriente de "
        "drenador, temperatura del encapsulado y curva de salida."
    ),
    "personal/recetas/paella.md": (
        "Receta de paella valenciana con arroz bomba,特点是 saffron y "
        "carne de pollo. Arroz, sofrito y fuego fuerte."
    ),
    "personal/informe.txt": "Informe anual de gastos, presupuesto y proveedores.",
    "viajes/futbol.md": "Calendario de partidos de futbol y resultados del club.",
    "viajes/informe-partidos.md": "Informe de partidos: goles, tarjetas y lesionados.",
}


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    for relative, text in DOCUMENTS.items():
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tree


@pytest.fixture()
def indexed(corpus: Path):
    database = SearchDatabase(corpus.parent / "index.db")
    Indexer(database).index_root(corpus)
    index = FuzzyIndex(database)
    index.rebuild()
    lexical = SearchEngine(database)
    return corpus, database, lexical, FuzzySearchEngine(lexical, index)


# -- blocking: bounded, deterministic, cost independent of size ---------------

def test_trigrams_are_deterministic_and_fold_accents():
    assert trigram_counts("Transistor") == trigram_counts("transistor")
    assert fold("polarización") == "polarizacion"
    assert "pol" in trigram_counts("polarización")


def test_a_huge_document_costs_the_same_as_a_tiny_one():
    """The whole point of the budget: cost per document, not per byte."""
    base = "el transistor bjt modelo ebers moll " * 4
    smaller = base + (" relleno " * 2_000)
    larger = base + (" relleno " * 20_000)
    frequency: dict[str, int] = {}

    kept_smaller = selective_trigrams(smaller, frequency)
    kept_larger = selective_trigrams(larger, frequency)
    kept_base = selective_trigrams(base, frequency)

    for kept in (kept_base, kept_smaller, kept_larger):
        assert 0 < len(kept) <= fuzzy.MAX_TRIGRAMS_PER_DOC
    # Ten times more text, the very same fingerprint: a single large file
    # cannot make the derived index explode. (The fingerprint can differ from
    # a short document's because of vocabulary, never because of length.)
    assert len(kept_larger) == len(kept_smaller)


def test_selective_trigrams_prefers_the_rare_words():
    # More distinct words than the 32-word budget, so the budget actually
    # bites and the choice of which words to keep becomes observable.
    words = [
        "transistor", "ebers", "moll", "modelo", "notas", "clase",
        "amplificador", "tension", "emisor", "polarizacion", "trabajo",
        "guia", "drenador", "encapsulado", "umbral", "puerta", "curva",
        "salida", "temperatura", "corriente", "colector", "base", "saturacion",
        "ganancia", "realimentacion", "frecuencia", "ancho", "banda",
        "ruido", "distorsion", "armonica", "fundamental",
        "comun", "comun", "comun", "comun", "comun", "comun",
    ]
    frequency = {word: 1 for word in set(words)}
    frequency["comun"] = 5  # appears in five documents
    frequency["tension"] = 3

    kept = selective_trigrams(" ".join(words), frequency)

    assert "tra" in kept  # the rare, identifying word is covered
    assert "com" not in kept  # the common one lost the 32-word budget


def test_no_single_word_monopolises_the_fingerprint_budget():
    """Round-robin exists because a long word used to eat the whole budget.

    With the words sorted longest-first, a 14-character word claimed a
    quarter of the 64 slots before any other word contributed, and the
    fingerprint stopped describing the document.
    """
    words = [
        "realimentacion", "saturacion", "amplificacion", "modulacion",
        "transistor", "ebers", "moll", "polarizacion", "encapsulado",
        "drenador", "colector", "ganancia", "frecuencia", "distorsion",
    ]
    frequency = {word: 1 for word in words}

    kept = selective_trigrams(" ".join(words), frequency)

    # Every selected word contributed at least one trigram.
    for word in words:
        assert any(
            word[index:index + 3] in kept
            for index in range(len(word) - 2)
        ), word


def test_the_fingerprint_covers_a_word_shared_by_several_documents():
    """The regression this phase's first implementation had.

    Selecting the *most selective trigrams* dropped the trigrams of a word
    that appears in two documents, so a query for that word blocked nothing.
    The fingerprint is now built from the document's most distinctive **words**
    (12 of them), so a shared-but-identifying word is still covered.
    """
    seven_documents = {
        "transistor": 2, "eers": 2, "moll": 1, "notas": 3, "clase": 4,
        "amplificador": 1, "tension": 3, "emisor": 1, "polarizacion": 2,
        "trabajo": 1, "guia": 1, "drenador": 1, "encapsulado": 1,
    }
    text = (
        "modelo ebers moll del transistor bjt con tension de polarizacion "
        "y punto de trabajo del amplificador en el emisor comun"
    )

    kept = selective_trigrams(text, seven_documents)

    # 'transistor' is in two documents out of seven, so a trigram-level
    # selectivity contest would have dropped it. The word-level budget keeps it.
    assert "tra" in kept
    assert "sis" in kept


def test_a_tiny_document_is_not_indexed_at_all():
    """Too small to discriminate would put it in everyone's candidate list."""
    assert selective_trigrams("ab", {}) == {}


# -- verification: this is where the truth is --------------------------------

def test_damerau_levenshtein_handles_the_transposition_typo():
    # The single most common typo shape. Plain Levenshtein scores it as 2,
    # which would push a 7-character word over its budget of 1.
    assert damerau_levenshtein("transsistor", "transistor", cap=2) == 1


def test_damerau_levenshtein_abandons_work_past_the_cap():
    assert damerau_levenshtein("abc", "xyzxyz", cap=1) > 1


@pytest.mark.parametrize(
    "token,expected",
    [("abcd", 1), ("abcdefg", 1), ("abcdefgh", 2), ("abc", 0), ("ab", 0)],
)
def test_edit_budget_grows_only_for_long_words(token: str, expected: int) -> None:
    assert edit_budget(token) == expected


def test_a_prefix_inside_the_text_is_accepted():
    """A prefix is a containment, and that is the rule that fires.

    ``transisto`` is literally inside ``transistor``, so the strongest rule
    wins before any distance is computed. This is the case the phase exists
    for, and it is also why no expensive work is needed for it.
    """
    resolved, rule, distance = resolve_token("transisto", "bjt.md", "el transistor")
    assert resolved is True
    assert rule == "exact-text"
    assert distance == 0


def test_a_prefix_inside_the_name_is_accepted():
    resolved, rule, _distance = resolve_token(
        "transisto", "transistor-datasheet.md", "sin el texto"
    )
    assert resolved is True
    assert rule == "exact-name"


def test_edit_rule_covers_a_transposition():
    resolved, rule, distance = resolve_token("transsistor", "bjt.md", "el transistor")
    assert resolved is True
    assert rule == "edit"
    assert distance == 1


def test_exact_match_wins_over_the_cheaper_rules():
    resolved, rule, _ = resolve_token("transistor", "transistor.md", "texto")
    assert resolved is True
    assert rule.startswith("exact")


def test_a_token_nothing_matches_is_rejected():
    resolved, rule, _distance = resolve_token("zzz", "bjt.md", "el transistor")
    assert resolved is False
    # Three characters get no edit budget at all: at that length almost every
    # word is one keystroke away from another.
    assert rule in {"no-match", "no-budget", "too-short"}


def test_a_short_token_is_rejected_before_any_work():
    assert resolve_token("ab", "bjt.md", "el transistor")[:2] == (False, "too-short")


def test_a_multi_token_query_needs_every_token(indexed):
    _corpus, _database, _lexical, engine = indexed
    # "transistor" is there; "futbol" is not: the intersection fails.
    resolved, _evidence = resolve_query(
        "transistor futbol", "bjt.md", "el transistor BJT"
    )
    assert resolved is False

    assert engine.search("transistor", limit=5)  # single token is fine


# -- the promises of the phase ------------------------------------------------

def test_prefix_query_finds_the_document(indexed):
    _corpus, _database, lexical, engine = indexed
    assert lexical.search("transisto", limit=5) == []  # lexical really misses
    results = engine.search("transisto", limit=5)
    assert [r.name for r in results] == ["bjt-modelo.md", "notas.txt"]


@pytest.mark.parametrize("typo", ["transsistor", "transistorr", "transltor"])
def test_typo_queries_find_the_document(indexed, typo: str):
    _corpus, _database, _lexical, engine = indexed
    results = engine.search(typo, limit=5)
    assert "bjt-modelo.md" in [r.name for r in results]


def test_accent_variant_finds_the_document(indexed):
    _corpus, _database, lexical, engine = indexed
    assert lexical.search("polarisacion", limit=5) == []
    results = engine.search("polarisacion", limit=5)
    assert "bjt-modelo.md" in [r.name for r in results]


def test_the_verifier_rejects_what_the_blocker_likes(tmp_path: Path):
    """The negative case the phase exists to guarantee.

    Two documents share almost all their trigrams, so the cheap filter ranks
    them identically. Only one of them actually contains the string. The one
    that does not must never be returned, however well it scored.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    decoy = tree / "decoy.txt"
    decoy.write_text(
        "una序列 de trigramas muy parecidos pero sin la palabra buscada: "
        "transicion transicion transicion",
        encoding="utf-8",
    )
    truth = tree / "truth.md"
    truth.write_text("aquí sí aparece transisto en una frase", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    index = FuzzyIndex(database)
    index.rebuild()
    engine = FuzzySearchEngine(SearchEngine(database), index)

    proposed = index.candidates(query_trigrams("transisto"), cap=10, minimum=0.0)

    # The decoy is proposed by the filter...
    assert any(document_id == _id_of(database, decoy) for document_id, _ in proposed)
    # ...and dropped by the verifier.
    results = engine.search("transisto", limit=5)
    assert [r.name for r in results] == ["truth.md"]


def _id_of(database: SearchDatabase, path: Path) -> str:
    from universal_search.domain.document import document_id_for

    return document_id_for("local", path)


# -- the contracts inherited from 026 -----------------------------------------

def test_lexical_results_are_returned_untouched(indexed):
    _corpus, _database, lexical, engine = indexed
    lexical_results = lexical.search("transistor", limit=5)
    hybrid_results = engine.search("transistor", limit=5)
    assert [r.document_id for r in hybrid_results] == [
        r.document_id for r in lexical_results
    ]
    assert [r.score for r in hybrid_results] == [r.score for r in lexical_results]


def test_filters_disable_the_layer(indexed):
    """A filter that excluded everything must not be undone by the fallback."""
    _corpus, _database, _lexical, engine = indexed
    assert engine.search("transisto", limit=5, source="onedrive") == []
    assert engine.search("transisto", limit=5, doc_type="pdf") == []


def test_nonsense_queries_stay_empty(indexed):
    _corpus, _database, _lexical, engine = indexed
    for nonsense in ("zzz no existe", "noexistenadaquienadie", "qqqq zzzz"):
        assert engine.search(nonsense, limit=5) == [], nonsense


def test_every_result_explains_itself(indexed):
    _corpus, _database, _lexical, engine = indexed
    result = engine.search("transsistor", limit=5)[0]
    assert result.explain and "fuzzy_matches" in result.explain
    match = result.explain["fuzzy_matches"][0]
    assert match["token"] == "transsistor"
    assert match["rule"] in {"substring", "edit"}
    assert result.explain_notes


def test_snippet_is_bounded(indexed):
    _corpus, _database, _lexical, engine = indexed
    result = engine.search("transisto", limit=5)[0]
    assert len(result.snippet) <= 210


# -- lifecycle: versioned, rebuildable, removable -----------------------------

def test_rebuild_is_versioned_and_counts_documents(indexed):
    _corpus, database, _lexical, _engine = indexed
    index = FuzzyIndex(database)
    assert index.count() == len(DOCUMENTS)
    assert index.version() == FUZZY_VERSION


def test_rebuild_is_deterministic(indexed):
    _corpus, database, _lexical, _engine = indexed
    index = FuzzyIndex(database)
    first = index.candidates(query_trigrams("transisto"), cap=50, minimum=0.0)
    index.rebuild()
    second = index.candidates(query_trigrams("transisto"), cap=50, minimum=0.0)
    assert first == second


def test_dirty_flag_triggers_a_lazy_rebuild(indexed):
    _corpus, database, _lexical, engine = indexed
    index = FuzzyIndex(database)
    index.remove_all()
    index.mark_dirty()
    assert index.is_dirty() is True

    # A search rebuilds lazily, so the layer is available again without the
    # user running a maintenance command.
    results = engine.search("transisto", limit=5)

    assert results
    assert index.is_dirty() is False
    assert index.count() == len(DOCUMENTS)


def test_removal_without_dirty_stays_lexical(indexed):
    _corpus, database, _lexical, engine = indexed
    FuzzyIndex(database).remove_all()
    assert engine.search("transisto", limit=5) == []


def test_remove_all_makes_search_lexical_only(indexed):
    _corpus, database, _lexical, engine = indexed
    FuzzyIndex(database).remove_all()
    assert engine.search("transisto", limit=5) == []


def test_engine_without_an_index_is_lexical_only(indexed):
    _corpus, _database, lexical, _index = indexed
    engine = FuzzySearchEngine(lexical, None)
    assert engine.search("transisto", limit=5) == []


# -- privacy ------------------------------------------------------------------

def test_forget_removes_the_fingerprint_rows(indexed):
    from universal_search.privacy import forget

    corpus, database, _lexical, _engine = indexed
    target = corpus / "electronica" / "bjt-modelo.md"
    document_id = _id_of(database, target)
    assert _fingerprint_rows(database, document_id)
    assert _fingerprinted_documents(database, document_id) == 1

    forget(database, target)

    assert _fingerprint_rows(database, document_id) == 0
    assert _fingerprinted_documents(database, document_id) == 0
    assert target.exists()  # the user's file is untouched


def _fingerprint_rows(database: SearchDatabase, document_id: str) -> int:
    """Postings for one document, through the surrogate mapping."""
    with database.connect() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM document_fuzzy_terms WHERE surrogate IN"
            " (SELECT surrogate FROM document_fuzzy_documents"
            "  WHERE document_id = ?)",
            (document_id,),
        ).fetchone()[0])


def _fingerprinted_documents(database: SearchDatabase, document_id: str) -> int:
    with database.connect() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM document_fuzzy_documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()[0])


def test_privacy_inventory_declares_the_fuzzy_tables():
    from universal_search.privacy import INVENTORY

    item = next(entry for entry in INVENTORY if entry.key == "fuzzy_index")
    assert "document_fuzzy_terms" in item.where
    assert "fingerprint" in item.what.lower() or "trigram" in item.what.lower()


@pytest.mark.parametrize("table", ["document_fuzzy_terms", "document_semantic"])
def test_indexer_delete_scrubs_the_optional_derived_tables(
    corpus: Path, tmp_path: Path, table: str
) -> None:
    """Removing a file from disk must not leave it reachable anywhere.

    Found by inspection while adding the fuzzy tables: the canonical delete
    path took the graph and the FTS rows but left the phase-026 semantic
    vectors behind, so a document deleted from disk survived as an orphan
    until someone ran a recovery command.
    """
    from universal_search.semantic import SemanticIndex

    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(corpus)
    FuzzyIndex(database).rebuild()
    SemanticIndex(database).rebuild()
    victim = corpus / "electronica" / "bjt-modelo.md"
    document_id = _id_of(database, victim)

    def rows_for() -> int:
        if table == "document_fuzzy_terms":
            return _fingerprint_rows(database, document_id)
        with database.connect() as connection:
            return int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0])

    assert rows_for()

    victim.unlink()
    Indexer(database).index_root(corpus)

    assert rows_for() == 0
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,)
        ).fetchone()[0] == 0


# -- the opt-out, symmetric with --no-semantic --------------------------------

def test_engine_factory_respects_the_opt_out(indexed):
    _corpus, database, _lexical, _engine = indexed
    FuzzyIndex(database).rebuild()
    disabled = fuzzy.engine_for(database, enabled=False)
    assert isinstance(disabled, SearchEngine)
    assert not isinstance(disabled, FuzzySearchEngine)
    enabled = fuzzy.engine_for(database, enabled=True)
    assert isinstance(enabled, FuzzySearchEngine)


def test_nonsense_guard_is_cheap_and_honest():
    assert is_nonsense("zzz") is True
    assert is_nonsense("qqqq") is True
    assert is_nonsense("transistor") is False
    assert is_nonsense("abc") is False
