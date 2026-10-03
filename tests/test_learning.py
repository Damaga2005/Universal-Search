"""Phase 044: local learning v2.

The tests are grouped by the contract they defend, not by the module they
touch. ``learn.py`` holds pure functions, so most of this file needs no
database at all; the rest builds a two-document index and asks the engine
questions a user could ask.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from universal_search import learn
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.ranking import (
    ACTIVATED_USAGE_WEIGHT,
    DEFAULT_WEIGHTS,
    Ranker,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


# -- the pure layer ------------------------------------------------------------

def test_a_single_open_is_worth_nothing():
    """One accidental click must not reorder anybody's results.

    This is the whole of cold start: it makes the floor a mechanism rather than
    a matter of taste. With zero or one event the answer is the baseline's
    answer, byte for byte.
    """
    assert learn.signal_for("d", [("examen", 1.0)], query="examen").is_inert
    assert learn.signal_for("d", [], query="examen").is_inert
    assert not learn.signal_for(
        "d", [("examen", 1.0)] * 3, query="examen"
    ).is_inert


def test_an_event_counts_for_the_query_it_was_recorded_under():
    """The gap phase 044 opened with: the ``query`` column was written since
    phase 008 and read by nothing but the user inspecting it.

    Four opens of a document under one query must say nothing at all about
    another: the general share is a fifth of the events, and four fifths of four
    is below the floor. Ten are needed before a differently-phrased request
    moves at all.
    """
    four_same = learn.signal_for("d", [("examen", 1.0)] * 4, query="examen")
    four_other = learn.signal_for("d", [("examen", 1.0)] * 4, query="receta")
    assert four_same.query_events == 4.0
    assert four_other.query_events == 0.0
    assert four_other.global_events == pytest.approx(4.0)
    assert four_same.boost == 1.0
    assert four_other.boost == 0.0

    ten_other = learn.signal_for("d", [("examen", 1.0)] * 10, query="receta")
    assert ten_other.boost > 0.0
    # And even once it acts, it stays below what the same history earns for the
    # query it was actually recorded under.
    assert ten_other.boost < four_same.boost


def test_enough_history_saturates_even_the_general_share():
    """Worth stating, because it is where the scoping stops helping.

    Twenty opens of one document saturate the general share too, so the query
    scoping is a *ranking* of evidence rather than a hard filter. Ten opens of
    unrelated work already act; twenty are as strong as the history that
    actually mentioned the query.
    """
    other = learn.signal_for("d", [("examen", 1.0)] * 20, query="receta")
    same = learn.signal_for("d", [("examen", 1.0)] * 4, query="examen")
    assert other.query_events == 0.0
    assert other.boost == 1.0
    assert other.boost == same.boost


def test_the_query_you_asked_for_gets_the_bulk_of_the_signal():
    same = learn.signal_for("d", [("examen", 1.0)] * 4, query="examen")
    other = learn.signal_for("d", [("examen", 1.0)] * 4, query="receta")
    assert same.query_events == 4.0
    assert other.query_events == 0.0
    assert same.boost > other.boost


def test_query_normalisation_is_exactly_as_narrow_as_it_sounds():
    """Case and whitespace, and nothing else.

    Stripping accents or punctuation would make two different requests compare
    equal, which is a worse failure than a missed boost.
    """
    assert learn.normalise_query("  Examen   FINAL ") == "examen final"
    assert learn.normalise_query("examen final") == "examen final"
    assert learn.normalise_query("exámen final") != "examen final"
    assert learn.normalise_query("examen-final") != "examen final"


def test_the_boost_saturates_and_never_exceeds_one():
    for count in range(2, 5000):
        assert 0.0 <= learn.signal_for(
            "d", [("q", 0.0)] * count, query="q"
        ).boost <= 1.0
    assert learn.signal_for("d", [("q", 0.0)] * 400, query="q").boost == 1.0


def test_decay_is_a_readable_step_not_a_curve():
    """Four buckets, so the forgetting schedule can be argued about.

    These are the numbers the phase report quotes. A step was chosen over an
    exponential because an exponential produces a value nobody can look at.
    """
    assert learn._decay(0) == 1.0
    assert learn._decay(30) == 1.0
    assert learn._decay(31) == 0.6
    assert learn._decay(90) == 0.6
    assert learn._decay(91) == 0.3
    assert learn._decay(365) == 0.3
    assert learn._decay(366) == learn.DECAY_FLOOR
    assert learn._decay(50_000) == learn.DECAY_FLOOR
    # Monotone: an older event is never worth more than a younger one.
    ages = [0, 45, 120, 400, 3000]
    shares = [learn._decay(age) for age in ages]
    assert shares == sorted(shares, reverse=True)


def test_a_clock_that_disagrees_is_not_evidence_of_age():
    assert learn._decay(-1) == 1.0
    assert learn._decay("not a number") == learn.DECAY_FLOOR


def test_old_habits_forget_and_recent_ones_do_not():
    recent = learn.signal_for("d", [("q", 0.0)] * 6, query="q")
    stale = learn.signal_for("d", [("q", 400.0)] * 6, query="q")
    assert recent.boost == 1.0
    assert stale.boost == 0.0


def test_the_explanation_says_what_actually_moved_it():
    signal = learn.signal_for("d", [("examen", 1.0)] * 3, query="examen")
    note = signal.note()
    assert "examen" in note
    assert "3" in note
    assert learn.signal_for("d", [("examen", 1.0)], query="examen").note() == ""


def test_summary_counts_what_the_gate_reports():
    events = {"a": [("q", 0.0)] * 4, "b": [("q", 0.0)], "c": []}
    signals = learn.signals_by_document(events, query="q")
    # "c" has no events at all, so it is absent rather than present-and-zero:
    # a caller iterating the result set cannot tell the difference, which is
    # the cold-start behaviour arrived at without a special case.
    assert set(signals) == {"a", "b"}
    report = learn.summary(signals)
    assert report["documents"] == 2.0
    assert report["with_effect"] == 1.0
    assert report["boost_max"] == pytest.approx(1.0)
    assert report["boost_sum"] == pytest.approx(1.0)


def test_learning_is_secondary_to_the_signal_it_is_compared_with():
    """The contract's arithmetic, pinned so a weight change cannot pass quietly.

    ``evaluation.learning_gate`` measures this over the corpus; this is the
    version that runs in milliseconds and explains itself if it fails.
    """
    total = DEFAULT_WEIGHTS.total
    usage_share = ACTIVATED_USAGE_WEIGHT / (total + ACTIVATED_USAGE_WEIGHT)
    recency_share = DEFAULT_WEIGHTS.recency / total
    assert usage_share < recency_share, (
        f"learning is {usage_share:.4f} of the score and recency "
        f"{recency_share:.4f}; learning must be the weaker signal"
    )
    # And both stay far below the explicit-intent signals, by a wide margin.
    exact_share = DEFAULT_WEIGHTS.filename_exact / total
    assert usage_share * 4 < exact_share


# -- the engine layer ----------------------------------------------------------

@pytest.fixture()
def engine(tmp_path):
    """A two-document index where both files answer to the query equally well."""
    root = tmp_path / "docs"
    root.mkdir()
    body = " ".join(["informe", "trimestral", "de", "ventas"] * 8)
    (root / "informe.md").write_text(body, encoding="utf-8")
    (root / "informe_detalle.md").write_text(body, encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(root)
    from universal_search.index.search import SearchEngine

    return SearchEngine(database)


def _ids_by_name(engine) -> dict[str, str]:
    with engine.database.connect() as connection:
        rows = connection.execute("SELECT id, name FROM documents").fetchall()
    return {row["name"]: row["id"] for row in rows}


def _open(engine, name: str, query: str, *, times: int = 4,
          age_days: float = 1.0) -> None:
    stamp = (NOW - timedelta(days=age_days)).isoformat(timespec="seconds")
    identifier = _ids_by_name(engine)[name]
    with engine.database.connect() as connection:
        for _ in range(times):
            connection.execute(
                "INSERT INTO usage_events (document_id, query, opened_at) "
                "VALUES (?, ?, ?)",
                (identifier, query, stamp),
            )
        connection.commit()


def _order(engine, query: str, *, usage: bool) -> list[str]:
    return [
        result.name
        for result in engine.search(query, limit=5, usage=usage, now=NOW)
    ]


def test_no_history_means_the_baseline_answer_exactly(engine):
    plain = engine.search("informe", limit=5, usage=False, now=NOW)
    learned = engine.search("informe", limit=5, usage=True, now=NOW)
    assert [r.name for r in plain] == [r.name for r in learned]
    assert [round(r.score, 12) for r in plain] == [
        round(r.score, 12) for r in learned
    ]


def test_a_repeated_workflow_is_promoted(engine):
    """A query neither file is *named* after, which is where learning applies.

    Querying "informe" would be the wrong experiment here: `informe.md` is an
    exact-name match, the ranker refuses to boost `informe_detalle.md` past
    it, and the workflow test would be measuring the guard instead of the
    signal.
    """
    before = _order(engine, "trimestral", usage=False)
    _open(engine, "informe_detalle.md", "trimestral", times=6)
    after = _order(engine, "trimestral", usage=True)
    assert before.index("informe_detalle.md") > after.index(
        "informe_detalle.md"
    ), f"baseline {before} -> learned {after}"


def test_the_promotion_is_explained_in_words(engine):
    _open(engine, "informe_detalle.md", "informe", times=6)
    results = engine.search("informe", limit=5, usage=True, explain=True, now=NOW)
    boosted = [r for r in results if (r.explain or {}).get("usage", 0.0) > 0.0]
    assert boosted
    for result in boosted:
        assert any("uso local" in note for note in result.explain_notes), (
            f"{result.name} moved with no sentence saying why: "
            f"{result.explain_notes}"
        )


def test_an_exact_filename_match_is_never_boosted(engine):
    """The corpus's exact-name queries are rare, so make one deliberately."""
    _open(engine, "informe_detalle.md", "informe", times=40)
    results = engine.search("informe", limit=5, usage=True, explain=True, now=NOW)
    exact = [r for r in results if (r.explain or {}).get("filename_exact", 0.0) > 0.0]
    assert exact, "the fixture must contain at least one exact-name match"
    for result in exact:
        assert (result.explain or {}).get("usage", 0.0) == 0.0


def test_a_stale_history_changes_nothing(engine):
    baseline = _order(engine, "informe", usage=False)
    _open(engine, "informe_detalle.md", "informe", times=8, age_days=900)
    assert _order(engine, "informe", usage=True) == baseline


def test_learning_cannot_resurrect_a_document_that_is_gone(tmp_path):
    """Orphaned events would keep a deleted file's query text alive forever.

    Exercised through the real path -- the file leaves the disk and a reindex
    reconciles -- because a bare ``DELETE FROM documents`` bypasses the
    indexer's cleanup and would pass with or without the fix.
    """
    root = tmp_path / "docs"
    root.mkdir()
    (root / "informe.md").write_text("informe trimestral", encoding="utf-8")
    victim = root / "informe_detalle.md"
    victim.write_text("informe trimestral", encoding="utf-8")
    database = SearchDatabase(tmp_path / "index.db")
    Indexer(database).index_root(root)

    from universal_search.index.search import SearchEngine

    engine = SearchEngine(database)
    _open(engine, "informe_detalle.md", "informe", times=6)
    with engine.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM usage_events"
        ).fetchone()[0] == 6

    victim.unlink()
    Indexer(database).index_root(root)
    with engine.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM usage_events"
        ).fetchone()[0] == 0


def test_the_effect_view_says_what_the_history_is_still_doing(engine):
    _open(engine, "informe_detalle.md", "informe", times=6)
    _open(engine, "informe_detalle.md", "informe", times=1, age_days=900)
    effects = engine.usage_effects(now=NOW)
    assert effects
    assert effects[0]["query"] == "informe"
    assert effects[0]["boost"] > 0.0
    assert effects[0]["note"]
    # An aged history is visible and inert, not invisible -- the user can see
    # that the row is still there and understand that it no longer counts.
    faded = engine.usage_effects(now=NOW + timedelta(days=900))
    assert faded[0]["boost"] == 0.0


def test_search_is_deterministic_with_a_history(engine):
    _open(engine, "informe_detalle.md", "informe", times=6)
    first = _order(engine, "informe", usage=True)
    for _ in range(3):
        assert _order(engine, "informe", usage=True) == first


# -- the ranker contract -------------------------------------------------------

def test_the_ranker_refuses_to_boost_an_exact_match_even_directly():
    """Belt and braces: the rule lives in the ranker, not only in the SQL.

    A caller that computes a boost by hand and hands it to ``signals`` gets the
    same answer, so the guarantee does not depend on which path produced the
    signal.
    """
    from tests.test_ranking_signals import candidate

    exact = candidate(name="bjt.md", content="bjt en el contenido")
    assert Ranker().signals(exact, ("bjt",), usage_boost=1.0, now=NOW)["usage"] == 0.0
    other = candidate(name="notas-bjt.md", content="bjt en el contenido")
    assert Ranker().signals(
        other, ("bjt",), usage_boost=1.0, now=NOW
    )["usage"] == 1.0