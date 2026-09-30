"""Phase 032: query suggestions, and the verification that defines them.

The property under test is not "it suggests something nice". It is that every
suggestion is a query that was run and returned a document, and that a query
with nothing to correct gets no advice at all.
"""

from pathlib import Path

import pytest

from universal_search.fuzzy import FuzzyIndex, FuzzySearchEngine, QuerySuggester
from universal_search.fuzzy.suggest import (
    Suggestion,
    correct_token,
    indexed_vocabulary,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


DOCUMENTS = {
    "electronica/bjt-modelo.md": (
        "Modelo Ebers-Moll del transistor. Corriente de colector y tension "
        "de polarizacion para el punto de trabajo."
    ),
    "electronica/notas.txt": (
        "Notas de clase del amplificador: emisor comun, ganancia de tension "
        "y respuesta en frecuencia del circuito."
    ),
    "personal/informe.txt": "Informe anual de gastos y presupuesto aprobado.",
    "viajes/futbol.md": "Calendario de partidos, goles y tarjetas del club.",
}


@pytest.fixture()
def indexed(tmp_path: Path):
    tree = tmp_path / "tree"
    for relative, text in DOCUMENTS.items():
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    fuzzy_index = FuzzyIndex(database)
    fuzzy_index.rebuild()
    lexical = SearchEngine(database)
    engine = FuzzySearchEngine(lexical, fuzzy_index)
    return tree, database, lexical, engine


# -- the vocabulary comes from the index and nothing else --------------------

def test_vocabulary_is_read_from_the_index_and_leaves_nothing_behind(indexed):
    _tree, database, _lexical, _engine = indexed
    with database.connect() as connection:
        vocabulary = indexed_vocabulary(connection)
        leftovers = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%vocab%'"
        ).fetchone()[0]
    assert vocabulary
    assert any(term == "transistor" for term, _count in vocabulary)
    # The view is created, read and dropped: storing it would cost space.
    assert leftovers == 0


def test_vocabulary_is_ordered_by_how_often_the_word_was_written(indexed):
    _tree, database, _lexical, _engine = indexed
    with database.connect() as connection:
        vocabulary = indexed_vocabulary(connection)
    counts = [count for _term, count in vocabulary]
    assert counts == sorted(counts, reverse=True)


# -- corrections come from real indexed words --------------------------------

def test_a_typo_is_corrected_to_an_indexed_word():
    vocabulary = [("transistor", 50), ("modelo", 10), ("corriente", 4)]
    assert correct_token("transsitor", vocabulary)[0] == ("transistor", 1)
    assert correct_token("modlo", vocabulary)[0] == ("modelo", 1)


def test_a_word_that_is_already_indexed_gets_no_correction():
    vocabulary = [("transistor", 50)]
    assert correct_token("transistor", vocabulary) == []


def test_a_short_token_gets_no_correction():
    # Below four characters almost everything is one edit away from something
    # else, so suggesting would be noise.
    assert correct_token("abc", [("abd", 10), ("xyz", 10)]) == []


def test_common_wins_at_the_same_distance():
    vocabulary = [("rareword", 1), ("transistor", 90)]
    corrections = correct_token("transisior", vocabulary)
    assert corrections[0][0] == "transistor"


def test_corrected_token_stays_within_the_edit_budget():
    vocabulary = [("completamente", 3)]
    # One deletion is inside the budget of two.
    assert correct_token("completamete", vocabulary)[0][0] == "completamente"
    # Three substitutions in a 13-character word are not: same length, so the
    # length filter cannot catch it, only the distance can.
    assert correct_token("comXXXetamente", vocabulary) == []


# -- the contract: a suggestion is a query that worked ----------------------

def test_a_suggestion_returns_a_real_document(indexed):
    _tree, _database, _lexical, engine = indexed
    suggester = QuerySuggester(engine)

    suggestions = suggester.suggest("transsitor")

    assert suggestions
    best = suggestions[0]
    assert best.token == "transsitor"
    assert best.replacement == "transistor"
    # The proof of the contract, executed here and not asserted in prose.
    assert engine.search(best.query, limit=1)
    assert best.results >= 1


def test_a_suggestion_is_explained(indexed):
    _tree, _database, _lexical, engine = indexed
    suggestion = QuerySuggester(engine).suggest("transsitor")[0]
    payload = suggestion.as_dict()
    assert payload["distance"] == 1
    assert payload["query"] and payload["replacement"]


def test_nonsense_gets_no_invented_advice(indexed):
    _tree, _database, _lexical, engine = indexed
    suggester = QuerySuggester(engine)
    for nonsense in ("zzz no existe", "qqqzzz wwwyyy", "noexistenadaquienadie"):
        assert suggester.suggest(nonsense) == [], nonsense


def test_a_well_formed_query_gets_no_suggestion(indexed):
    _tree, _database, _lexical, engine = indexed
    assert QuerySuggester(engine).suggest("transistor") == []


def test_a_query_with_nothing_indexed_gets_no_suggestion(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "unico.md").write_text("contenido", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(tree)
    engine = SearchEngine(database)
    # The index contains no word close to "zzz" or "modelo": nothing to offer.
    assert QuerySuggester(engine).suggest("modelo") == []


def test_suggestions_respect_the_limit_and_are_ordered(indexed):
    _tree, _database, _lexical, engine = indexed
    suggester = QuerySuggester(engine, max_suggestions=2)
    suggestions = suggester.suggest("transsitor modlo", limit=2)
    assert len(suggestions) <= 2
    assert [item.distance for item in suggestions] == sorted(
        item.distance for item in suggestions
    )


def test_a_token_without_a_verified_correction_is_left_alone(indexed):
    _tree, _database, _lexical, engine = indexed
    suggester = QuerySuggester(engine)
    # "zzz" has no near neighbour among the indexed words, so it cannot be
    # corrected, and "zzz transsitor" as a whole returns nothing even after
    # correcting the second token -- so nothing is offered at all. That is the
    # contract: a suggestion must be a query that works, and this one is not.
    assert suggester.suggest("zzz transsitor") == []


def test_suggester_works_with_an_explicit_vocabulary(indexed):
    """The vocabulary is injectable so the rule can be tested without an index."""
    _tree, _database, _lexical, engine = indexed
    suggester = QuerySuggester(engine, vocabulary=lambda: [("transistor", 50)])
    assert suggester.suggest("transsitor")[0].replacement == "transistor"


def test_suggester_survives_a_build_without_vocabulary_support(
    indexed, monkeypatch
):
    """No fts5vocab means no suggestions, never a broken search."""
    _tree, _database, _lexical, engine = indexed
    monkeypatch.setattr(
        "universal_search.fuzzy.suggest.indexed_vocabulary",
        lambda connection: [],
    )
    assert QuerySuggester(engine).suggest("transsitor") == []


def test_suggestion_dataclass_is_immutable():
    suggestion = Suggestion("a b", "a", "b", 1, 3)
    with pytest.raises(Exception):
        suggestion.query = "otro"  # type: ignore[misc]
