"""The measurement instrument itself, and the regressions it pins.

Two layers:

* pure tests of the metric definitions (Precision@K, Recall@K, MRR) and
  the corpus contract, which must hold regardless of ranking;
* end-to-end tests over the labelled corpus, including a comparison with
  the committed baseline ``evaluation/baseline.json`` so any change that
  moves a ranking has to be justified by a measurement.
"""

import json
from pathlib import Path

import pytest

from evaluation import corpus as corpus_module
from evaluation import experiments
from evaluation.metrics import (
    first_relevant_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_query,
)
from evaluation.runner import K_VALUES, measure

BASELINE = Path(__file__).resolve().parents[1] / "evaluation" / "baseline.json"


# -- metric definitions --------------------------------------------------------

def test_precision_at_k_divides_by_k():
    ranked = ["a", "b", "c", "d"]
    assert precision_at_k(ranked, {"a", "c"}, 2) == 0.5
    assert precision_at_k(ranked, {"a", "c"}, 4) == 0.5
    # Fewer results than k: the missing slots count against precision.
    assert precision_at_k(["a"], {"a"}, 3) == pytest.approx(1 / 3)
    assert precision_at_k(ranked, {"z"}, 3) == 0.0


def test_recall_at_k_divides_by_the_number_of_relevant_documents():
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"a", "z"}, 2) == 0.5
    assert recall_at_k(ranked, {"a", "b"}, 2) == 1.0
    assert recall_at_k(ranked, {"a", "z"}, 4) == 0.5
    # An empty relevance set only scores 1.0 when nothing was retrieved.
    assert recall_at_k([], set(), 3) == 1.0
    assert recall_at_k(ranked, set(), 3) == 0.0


def test_reciprocal_rank_and_first_relevant_position():
    ranked = ["x", "y", "a", "b"]
    assert first_relevant_rank(ranked, {"a", "b"}) == 3
    assert first_relevant_rank(ranked, {"z"}) is None
    assert reciprocal_rank(ranked, {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranked, {"z"}) == 0.0
    # The first position gives the maximum possible reciprocal rank.
    assert reciprocal_rank(["a", "b"], {"a", "b"}) == 1.0


def test_empty_relevance_set_rewards_silence():
    # "zzz no existe" must retrieve nothing: that is a perfect score, not a
    # division by zero and not a free pass.
    assert precision_at_k([], set(), 3) == 1.0
    assert recall_at_k([], set(), 3) == 1.0
    assert reciprocal_rank([], set()) == 1.0
    # Retrieving anything at all fails the query.
    assert precision_at_k(["a"], set(), 3) == 0.0
    assert recall_at_k(["a"], set(), 3) == 0.0
    assert reciprocal_rank(["a"], set()) == 0.0


def test_metrics_reject_non_positive_cutoffs():
    with pytest.raises(ValueError):
        precision_at_k(["a"], {"a"}, 0)
    with pytest.raises(ValueError):
        recall_at_k(["a"], {"a"}, -1)


def test_score_query_aggregates_every_cutoff():
    score = score_query("q", ["a", "b", "c"], {"a", "b"}, (1, 2, 3), [0.9, 0.5, 0.1])
    assert score.precision == {1: 1.0, 2: 1.0, 3: 2 / 3}
    assert score.recall == {1: 0.5, 2: 1.0, 3: 1.0}
    assert score.first_relevant == 1
    assert score.reciprocal_rank == 1.0
    assert score.intruders(3) == ("c",)
    assert score.missing() == ()
    # Best relevant 0.9 minus best non-relevant 0.1.
    assert score.relevance_margin() == pytest.approx(0.8)
    # 0.9 (first relevant) minus 0.5 (last relevant).
    assert score.top_spread() == pytest.approx(0.4)


def test_relevance_margin_is_none_when_a_group_is_absent():
    assert score_query("q", ["a"], {"a"}).relevance_margin() is None
    assert score_query("q", ["a"], set()).relevance_margin() is None


# -- corpus contract -----------------------------------------------------------

def test_corpus_labels_reference_existing_documents():
    corpus_module.assert_labels_are_consistent()
    assert len(corpus_module.DOCUMENTS) == len(corpus_module.DOCUMENT_IDS)
    # Every document is a distinct path too (the index keys on it).
    paths = [document.path for document in corpus_module.DOCUMENTS]
    assert len(paths) == len(set(paths))
    # The spec's required query cases are all covered.
    queries = {labelled.query for labelled in corpus_module.LABELLED_QUERIES}
    for required in (
        "BJT", '"ebers moll"', "ebers moll", "CMOS", "MUX", "informe",
        "polarizacion", "notas", "type:pdf", "bjt type:txt",
    ):
        assert required in queries


def test_corpus_is_byte_for_byte_deterministic(tmp_path: Path):
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    corpus_module.build(first_root)
    corpus_module.build(second_root)

    for document in corpus_module.DOCUMENTS:
        one = (first_root / document.path).read_bytes()
        two = (second_root / document.path).read_bytes()
        assert one == two, document.id
        assert (first_root / document.path).stat().st_mtime == (
            second_root / document.path
        ).stat().st_mtime


# -- end to end over the real engine -------------------------------------------

@pytest.fixture(scope="module")
def corpus_env(tmp_path_factory):
    """Index the labelled corpus once for the whole module."""
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer
    from universal_search.index.search import SearchEngine

    root = tmp_path_factory.mktemp("evaluation-corpus")
    tree = root / "tree"
    corpus_module.build(tree)
    database = SearchDatabase(root / "index.db")
    Indexer(database).index_root(tree)
    return {
        "tree": tree,
        "database": database,
        "engine": SearchEngine(database),
    }


@pytest.fixture(scope="module")
def report(corpus_env):
    return measure(
        corpus_env["engine"], corpus_module.ids_by_path(corpus_env["tree"])
    )


def test_every_labelled_query_reports_a_relevant_document_first(report):
    # MRR of 1.0 means the first result of every non-empty query is
    # relevant; this is the headline number the phase is judged on.
    assert report.mrr() == pytest.approx(1.0)
    assert report.mean_precision(1) == pytest.approx(1.0)
    assert report.mean_recall(3) >= 0.90


def test_ranking_matches_the_committed_baseline(report):
    """Regression fixture: the ranked head of every query must not move.

    A change that reorders these lists has to update the baseline with a
    measurement that justifies it, not silently.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = {
        score.query: list(score.ranked[:3]) for score in report.scores
    }
    assert current == baseline["top"]
    assert report.mrr() == pytest.approx(baseline["aggregates"]["mrr"])
    for k in K_VALUES:
        assert report.mean_precision(k) == pytest.approx(
            baseline["aggregates"]["mean_precision_at_k"][str(k)]
        )
        assert report.mean_recall(k) == pytest.approx(
            baseline["aggregates"]["mean_recall_at_k"][str(k)]
        )


def test_phrase_outranks_the_same_words_far_apart(report):
    scores = report.by_query()
    phrase = scores['"ebers moll"']
    assert phrase.ranked[0] == "ebers-exacto"
    # ebers-lejos mentions both words but never next to each other, so it
    # must sit below every document with an adjacent occurrence.
    assert phrase.ranked.index("ebers-lejos") > phrase.ranked.index("bjt-modelo")
    # The proximity signal is what separates them, measurably.
    scattered = phrase.points[phrase.ranked.index("ebers-lejos")]["proximity"]
    adjacent = phrase.points[phrase.ranked.index("bjt-modelo")]["proximity"]
    assert scattered < adjacent


def test_path_noise_never_outranks_relevant_content(report):
    scores = report.by_query()
    # Accidental generic path match: a CV inside descargas/notas/.
    notas = scores["notas"]
    assert notas.ranked[0] == "bjt-notas"
    assert notas.ranked[1] == "notas-generico"
    # It is retrieved (the index is honest) but loses by a clear margin.
    assert (notas.relevance_margin() or 0.0) > 0.20
    # Its points come almost entirely from the path, never from content.
    intruder = notas.points[notas.ranked.index("notas-generico")]
    assert intruder["path_match"] > 0.0
    assert intruder["phrase_exact"] == 0.0
    assert intruder["filename_tokens"] == 0.0

    # Path-only match for a single term: ranks below every content match.
    bjt = scores["BJT"]
    assert bjt.ranked.index("bjt-carpeta") > bjt.ranked.index("bjt-amplificador")
    assert bjt.ranked.index("bjt-carpeta") > bjt.ranked.index("bjt-modelo")


def test_length_normalisation_stops_a_long_document_winning_by_bulk(report):
    scores = report.by_query()
    bjt = scores["BJT"]
    log_position = bjt.ranked.index("bjt-log")
    focused = bjt.ranked.index("bjt-amplificador")
    assert log_position > focused
    # 4000 words with two mentions: BM25 marks it down against the focused
    # documents, which is the length normalisation doing its job.
    assert bjt.scores[log_position] < bjt.scores[focused]
    assert bjt.points[log_position]["bm25"] < bjt.points[focused]["bm25"]


def test_metadata_only_binary_is_retrievable_by_name_and_by_filter(report):
    scores = report.by_query()
    assert "bjt-datasheet" in scores["BJT"].ranked
    # Filter-only query: no text to rank, score 0.0, recency order.
    pdf = scores["type:pdf"]
    assert list(pdf.ranked) == ["bjt-datasheet"]
    assert list(pdf.scores) == [0.0]
    # Nothing was scored at all: a filter-only query bypasses the ranker,
    # so there is no signal breakdown to explain.
    assert pdf.points[0] == {}


def test_a_query_with_no_match_retrieves_nothing(report):
    empty = report.by_query()["zzz no existe"]
    assert empty.ranked == ()
    assert empty.reciprocal_rank == 1.0
    assert empty.precision[1] == 1.0


def test_measurement_is_reproducible_across_repeated_runs(report, corpus_env):
    # Re-running the same engine over the same corpus must not move a
    # single position: no clock, no RNG, no cache-dependent ordering.
    second = measure(
        corpus_env["engine"], corpus_module.ids_by_path(corpus_env["tree"])
    )
    assert [score.ranked for score in report.scores] == [
        score.ranked for score in second.scores
    ]


def test_filename_evidence_outranks_content_only_evidence(report):
    scores = report.by_query()
    informe = scores["informe"]
    # A file named exactly "informe" outranks one that merely repeats the
    # word eight times. This ordering is a weighting decision, and the
    # evaluation is what makes it visible instead of assumed.
    assert informe.ranked[0] == "informe"
    assert (
        informe.ranked.index("informe-etiqueta")
        < informe.ranked.index("bitacora")
    )
    # The content-only document is still inside the top 3.
    assert informe.ranked.index("bitacora") < 3


def test_recency_breaks_ties_between_equal_documents(report):
    diagrama = report.by_query()["diagrama"]
    # Newer first even though its path sorts last: the recency signal wins
    # the tie and the ascending path is only the fallback.
    assert list(diagrama.ranked) == ["diagrama-almacen", "diagrama-lecturas"]
    assert diagrama.scores[0] > diagrama.scores[1]


# -- measured headroom of the weights (evaluation/experiments.py) --------------

def test_path_noise_needs_a_sevenfold_weight_increase_to_displace_content(
    corpus_env,
):
    """The "path must not dominate content" guard rail, as a measurement.

    ``bjt-carpeta`` only matches through its folder. It cannot overtake a
    content match until ``path_match`` grows from 0.8 to ~5.75.
    """
    result = experiments.flip_point(
        corpus_env["tree"], corpus_env["database"], "path_match", "BJT"
    )
    assert result["flip_up_at"] is not None
    assert result["headroom_ratio"] > 5.0
    # ...and when it does, the path-only document is the one that rises.
    assert "bjt-carpeta" in result["top_at_flip"]


def test_recency_is_load_bearing_for_the_tie_pair(corpus_env):
    result = experiments.flip_point(
        corpus_env["tree"], corpus_env["database"], "recency", "diagrama"
    )
    assert result["load_bearing"] is True
    # With recency switched off the ranking falls back to the ascending
    # path, which reverses the pair.
    assert result["top_at_zero"] == ["diagrama-lecturas", "diagrama-almacen"]


def test_filename_exact_is_the_tightest_boundary_in_the_system(corpus_env):
    result = experiments.flip_point(
        corpus_env["tree"], corpus_env["database"], "filename_exact", "informe"
    )
    assert result["load_bearing"] is True
    # Under 1.3x its current value the two exact-name documents swap: this
    # is the number to know before touching it.
    assert result["headroom_ratio"] < 1.5
