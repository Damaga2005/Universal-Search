"""One test per ranking signal, plus robustness and explainability.

The evaluation corpus (phase 013) measures end-to-end quality; this file
pins the behaviour underneath it, signal by signal, so a weight change or
a refactor of the hot path cannot quietly alter a single contribution.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from universal_search.index.ranking import (
    ACTIVATED_CONTEXT_WEIGHT,
    ACTIVATED_USAGE_WEIGHT,
    DEFAULT_WEIGHTS,
    FREQ_SATURATION,
    NEUTRAL_RECENCY,
    RECENCY_FLOOR,
    Candidate,
    Ranker,
    RankingWeights,
    clear_caches,
)

NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def candidate(
    *,
    name: str = "documento.md",
    path: str = "C:/carpeta/documento.md",
    content: str | None = "contenido de ejemplo",
    source: str = "local",
    modified_at: str | None = "2024-01-01T00:00:00+00:00",
    bm25_rank: float = -1.0,
) -> Candidate:
    return Candidate(
        name=name,
        path=path,
        content=content,
        source=source,
        modified_at=modified_at,
        bm25_rank=bm25_rank,
    )


def signals_for(item: Candidate, terms: tuple[str, ...], **kwargs) -> dict:
    return Ranker().signals(item, terms, now=NOW, **kwargs)


# -- configuration is centralised and immutable --------------------------------

def test_default_weights_are_centralised_and_sum_to_the_documented_total():
    # docs/RANKING.md and the module docstring both state 14.0.
    assert DEFAULT_WEIGHTS.total == pytest.approx(14.0)
    assert all(weight >= 0.0 for weight in dataclasses.astuple(DEFAULT_WEIGHTS))
    # Personal signals ship switched off.
    assert DEFAULT_WEIGHTS.usage == 0.0
    assert DEFAULT_WEIGHTS.context == 0.0


def test_weights_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_WEIGHTS.recency = 9.0  # type: ignore[misc]


def test_signal_names_and_weight_fields_never_drift():
    ranker = Ranker()
    names = set(ranker.signals(candidate(), ("documento",)))
    fields = {field.name for field in dataclasses.fields(RankingWeights)}
    assert names == fields


# -- individual signals --------------------------------------------------------

def test_filename_exact_matches_the_name_or_its_stem_only():
    assert signals_for(candidate(name="bjt.md"), ("bjt",))["filename_exact"] == 1.0
    # The stem is everything before the LAST dot.
    assert signals_for(candidate(name="bjt.pdf"), ("bjt",))["filename_exact"] == 1.0
    # A name that merely contains the term is not an exact match...
    assert (
        signals_for(candidate(name="notas-bjt.md"), ("bjt",))["filename_exact"]
        == 0.0
    )
    # ...and neither is a middle segment of a multi-extension name.
    assert (
        signals_for(candidate(name="manual.bjt.pdf"), ("bjt",))["filename_exact"]
        == 0.0
    )
    assert (
        signals_for(candidate(name="bjt.md"), ("transistor",))["filename_exact"]
        == 0.0
    )


def test_filename_tokens_are_the_fraction_of_query_terms_present():
    item = candidate(name="ebers_moll_teoria.md")
    assert signals_for(item, ("ebers", "moll"))["filename_tokens"] == 1.0
    assert signals_for(item, ("ebers", "teoria", "cmos"))["filename_tokens"] == 2 / 3
    assert signals_for(item, ("cmos",))["filename_tokens"] == 0.0
    assert signals_for(item, ())["filename_tokens"] == 0.0


def test_path_match_reads_parent_directories_and_ignores_short_terms():
    item = candidate(path="C:/cursos/BJT/electronica/hoja.md")
    assert signals_for(item, ("bjt",))["path_match"] == 1.0
    # Single characters are excluded so drive letters cannot dominate.
    assert signals_for(item, ("c",))["path_match"] == 0.0
    # The file name itself is not a path signal (that is filename_tokens).
    assert signals_for(candidate(path="C:/x/bjt.md"), ("bjt",))["path_match"] == 0.0
    assert signals_for(item, ("bjt", "electronica"))["path_match"] == 1.0
    assert signals_for(item, ("bjt", "ausente"))["path_match"] == 0.5


def test_phrase_requires_adjacent_tokens_in_the_query_order():
    near = candidate(content="el transistor polar de punto fijo")
    far = candidate(content="el transistor con polar hoy")
    assert signals_for(near, ("transistor", "polar"))["phrase_exact"] == 1.0
    assert signals_for(far, ("transistor", "polar"))["phrase_exact"] == 0.0
    # Reversed order is not a phrase either.
    assert signals_for(near, ("polar", "transistor"))["phrase_exact"] == 0.0
    # A single term is a phrase when it occurs at all.
    assert signals_for(near, ("transistor",))["phrase_exact"] == 1.0
    assert signals_for(near, ("ausente",))["phrase_exact"] == 0.0
    assert signals_for(near, ())["phrase_exact"] == 0.0


def test_term_frequency_saturates_and_averages_over_query_terms():
    dense = candidate(content="bjt " * int(FREQ_SATURATION) + "bjt " * 50)
    assert signals_for(dense, ("bjt",))["term_freq"] == 1.0
    once = candidate(content="una mencion de bjt en medio")
    assert signals_for(once, ("bjt",))["term_freq"] == pytest.approx(0.1)
    # One of two terms present: the mean halves.
    assert (
        signals_for(once, ("bjt", "ausente"))["term_freq"] == pytest.approx(0.05)
    )
    assert signals_for(once, ("bjt",))["term_freq"] < signals_for(
        dense, ("bjt",)
    )["term_freq"]


def test_proximity_measures_the_tightest_window():
    adjacent = candidate(content="ebers moll")
    scattered = candidate(
        content="ebers " + "palabra " * 20 + "moll"
    )
    assert signals_for(adjacent, ("ebers", "moll"))["proximity"] == 1.0
    assert 0.0 < signals_for(scattered, ("ebers", "moll"))["proximity"] < 1.0
    # A single term is always at distance 1 from itself.
    assert signals_for(scattered, ("ebers",))["proximity"] == 1.0
    # A missing term makes proximity undefined (0.0), not 1.0.
    assert signals_for(scattered, ("ebers", "ausente"))["proximity"] == 0.0
    assert signals_for(adjacent, ())["proximity"] == 0.0


def test_bm25_is_mapped_monotonically_into_zero_one():
    assert signals_for(candidate(bm25_rank=-1.0), ("x",))["bm25"] == pytest.approx(0.5)
    assert signals_for(candidate(bm25_rank=-9.0), ("x",))["bm25"] == pytest.approx(0.9)
    assert signals_for(candidate(bm25_rank=0.0), ("x",))["bm25"] == 0.0
    # Defensive: a positive rank (never produced by FTS5) cannot go negative.
    assert signals_for(candidate(bm25_rank=3.0), ("x",))["bm25"] == 0.0
    assert (
        signals_for(candidate(bm25_rank=-9.0), ("x",))["bm25"]
        > signals_for(candidate(bm25_rank=-1.0), ("x",))["bm25"]
    )


def test_doc_type_separates_extractable_content_from_metadata_only():
    assert signals_for(candidate(content="texto"), ("x",))["doc_type"] == 1.0
    assert signals_for(candidate(content=None), ("x",))["doc_type"] == 0.4
    # Empty text is treated as metadata-only, not as a content match.
    assert signals_for(candidate(content=""), ("x",))["doc_type"] == 0.4


def test_source_is_a_neutral_extension_point_today():
    for source in ("local", "onedrive", "other", "desconocido"):
        assert signals_for(candidate(source=source), ("x",))["source"] == 1.0


def test_recency_is_bounded_monotone_and_never_buries_old_documents():
    fresh = signals_for(candidate(modified_at="2024-06-01T00:00:00+00:00"), ("x",))
    old = signals_for(candidate(modified_at="2000-01-01T00:00:00+00:00"), ("x",))
    assert fresh["recency"] == pytest.approx(1.0)
    assert old["recency"] == pytest.approx(RECENCY_FLOOR, abs=1e-3)
    assert RECENCY_FLOOR <= old["recency"] <= fresh["recency"] <= 1.0
    # A future timestamp is clamped instead of exceeding 1.0.
    future = signals_for(candidate(modified_at="2099-01-01T00:00:00+00:00"), ("x",))
    assert future["recency"] == pytest.approx(1.0)


def test_missing_metadata_is_handled_without_crashing():
    for modified_at in (None, "", "no-es-una-fecha", "2024-13-45"):
        item = candidate(modified_at=modified_at)
        assert signals_for(item, ("x",))["recency"] == NEUTRAL_RECENCY
    empty = candidate(name="", path="", content=None, source="", modified_at=None,
                      bm25_rank=0.0)
    values = signals_for(empty, ())
    assert all(0.0 <= value <= 1.0 for value in values.values())
    # With no query terms only the metadata baseline remains: metadata-only
    # doc_type, neutral source and neutral recency.
    neutral_baseline = (
        DEFAULT_WEIGHTS.doc_type * 0.4
        + DEFAULT_WEIGHTS.source * 1.0
        + DEFAULT_WEIGHTS.recency * NEUTRAL_RECENCY
    ) / DEFAULT_WEIGHTS.total
    assert Ranker().score(empty, ()) == pytest.approx(neutral_baseline)


def test_a_very_long_document_keeps_every_signal_bounded():
    long_content = "palabra " * 50_000 + " bjt " + "palabra " * 50_000
    item = candidate(name="bitacora.md", content=long_content, bm25_rank=-0.5)
    values = signals_for(item, ("bjt",))
    assert all(0.0 <= value <= 1.0 for value in values.values())
    # One mention in 100k words: frequency is diluted, not saturated.
    assert values["term_freq"] == pytest.approx(0.1)
    assert 0.0 <= values["bm25"] < 0.34
    assert 0.0 <= Ranker().score(item, ("bjt",)) <= 1.0


# -- personal signals stay optional and separate -------------------------------

def test_personal_signals_are_off_unless_they_are_supplied():
    item = candidate(name="bjt.md", content="bjt en el contenido")
    base = Ranker().signals(item, ("bjt",), now=NOW)
    assert base["usage"] == 0.0
    assert base["context"] == 0.0

    boosted = Ranker().signals(
        item, ("bjt",), usage_boost=0.8, context_boost=0.4, now=NOW
    )
    assert boosted["usage"] == 0.8
    assert boosted["context"] == 0.4
    # Textual relevance is untouched by the personal layer.
    textual = {"filename_exact", "filename_tokens", "phrase_exact", "term_freq",
               "proximity", "bm25", "path_match", "doc_type", "source", "recency"}
    assert {k: boosted[k] for k in textual} == {k: base[k] for k in textual}


def test_activated_personal_weights_keep_the_score_normalized():
    item = candidate(content="bjt repetido bjt")
    weights = RankingWeights(
        usage=ACTIVATED_USAGE_WEIGHT, context=ACTIVATED_CONTEXT_WEIGHT
    )
    ranker = Ranker(weights)
    for usage in (0.0, 0.5, 1.0):
        for context in (0.0, 0.5, 1.0):
            score = ranker.score(
                item, ("bjt",), usage_boost=usage, context_boost=context, now=NOW
            )
            assert 0.0 <= score <= 1.0


def test_personal_boosts_are_clamped_to_the_unit_interval():
    values = signals_for(candidate(), ("x",), usage_boost=9.0, context_boost=-4.0)
    assert values["usage"] == 1.0
    assert values["context"] == 0.0


# -- explainability ------------------------------------------------------------

def test_contributions_expose_every_signal_with_its_weight_and_total():
    item = candidate(name="bjt.md", content="bjt con polarizacion", bm25_rank=-3.0)
    points, total = Ranker().contributions(item, ("bjt",), now=NOW)
    values = Ranker().signals(item, ("bjt",), now=NOW)
    assert points.keys() == values.keys()
    for name, value in values.items():
        expected = getattr(DEFAULT_WEIGHTS, name) * value
        assert points[name] == pytest.approx(expected)
    assert total == pytest.approx(sum(points.values()) / DEFAULT_WEIGHTS.total)
    assert 0.0 <= total <= 1.0


def test_explanation_contains_each_inspectable_component():
    points, total = Ranker().contributions(
        candidate(name="bjt.md", content="bjt bjt bjt", bm25_rank=-2.0),
        ("bjt",),
        now=NOW,
    )
    for component in (
        "filename_exact",      # filename contribution
        "filename_tokens",
        "path_match",          # path contribution
        "phrase_exact",        # content contribution
        "term_freq",
        "proximity",           # proximity
        "bm25",
        "doc_type",            # type/source contribution
        "source",
        "recency",
        "usage",               # optional personal signal
        "context",
    ):
        assert component in points
    assert total > 0.0


# -- determinism ---------------------------------------------------------------

def test_identical_candidates_produce_identical_signals():
    first = signals_for(candidate(name="bjt.md"), ("bjt", "moll"))
    second = signals_for(candidate(name="bjt.md"), ("bjt", "moll"))
    assert first == second


def test_clearing_caches_does_not_change_any_result():
    item = candidate(name="bjt.md", content="bjt " * 40 + "moll")
    warm = Ranker().signals(item, ("bjt", "moll"), now=NOW)
    clear_caches()
    cold = Ranker().signals(item, ("bjt", "moll"), now=NOW)
    assert cold == warm


def test_repeated_terms_do_not_change_the_score():
    item = candidate(name="nota.md", content="nota antigua con nota repetida")
    once = Ranker().score(item, ("nota",), now=NOW)
    thrice = Ranker().score(item, ("nota", "nota", "nota"), now=NOW)
    assert once == pytest.approx(thrice)
