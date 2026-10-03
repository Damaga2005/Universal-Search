"""Ranking quality metrics (spec 013): Precision@K, Recall@K and MRR.

Definitions, for a ranked list of document ids and a set of relevant ids:

* ``Precision@K`` — relevant ids among the first K, divided by K (the
  textbook definition: a query that returns fewer than K results is
  penalised, which is the honest reading for a search box).
* ``Recall@K`` — relevant ids among the first K, divided by the total
  number of relevant ids.
* ``MRR`` (mean reciprocal rank) — ``1 / rank`` of the first relevant id,
  ``0.0`` when none is retrieved.

A query with an **empty relevance set** is the "must retrieve nothing"
case. All three metrics are then ``1.0`` for an empty result list and
``0.0`` otherwise, so a corpus full of noise can never be rewarded.

Phase 045 adds the three the phase asks for and the repository did not have.
``DEFAULT_K_VALUES`` now includes ``10`` because P@1/5/10 and R@5/10 are the
phase's numbers and a harness that cannot produce P@10 cannot report them.

* **filter accuracy** -- of the queries carrying a filter, how many returned
  *only* results satisfying it. A relevance metric cannot see this: a query can
  have a perfect P@5 and still hand four PDFs back to somebody who asked for
  text.
* **zero-result accuracy** -- of the queries that must return nothing, how many
  did. Separate from the empty-set branch above because that branch rewards
  silence *inside* a P@K; this asks the question on its own terms and
  aggregates it, and the committed corpus had exactly one such query, which is
  not a number.
* **exact-match accuracy** already existed in ``runner.py``; it is re-exported
  here so that one import answers "how good is this?" completely.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

#: The K values every report is measured at. Phase 045.
DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)


def precision_at_k(
    ranked: Sequence[str], relevant: Collection[str], k: int
) -> float:
    """Relevant ids in the first ``k`` positions, over ``k``."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        return 1.0 if not ranked else 0.0
    hits = sum(1 for document_id in list(ranked)[:k] if document_id in relevant)
    return hits / k


def recall_at_k(
    ranked: Sequence[str], relevant: Collection[str], k: int
) -> float:
    """Relevant ids in the first ``k`` positions, over all relevant ids."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        return 1.0 if not ranked else 0.0
    hits = sum(1 for document_id in list(ranked)[:k] if document_id in relevant)
    return hits / len(relevant)


def first_relevant_rank(
    ranked: Sequence[str], relevant: Collection[str]
) -> int | None:
    """1-based position of the first relevant id, or ``None``."""
    for position, document_id in enumerate(ranked, start=1):
        if document_id in relevant:
            return position
    return None


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    """Reciprocal rank of the first relevant id (0.0 when absent)."""
    if not relevant:
        return 1.0 if not ranked else 0.0
    position = first_relevant_rank(ranked, relevant)
    return 0.0 if position is None else 1.0 / position


@dataclass(frozen=True, slots=True)
class QueryScore:
    """Measured outcome of one labelled query."""

    query: str
    relevant: frozenset[str]
    ranked: tuple[str, ...]
    precision: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    reciprocal_rank: float = 0.0
    first_relevant: int | None = None
    scores: tuple[float, ...] = ()
    points: tuple[dict[str, float], ...] = ()

    def intruders(self, k: int) -> tuple[str, ...]:
        """Ids inside the first ``k`` that the labels do not call relevant."""
        return tuple(
            document_id
            for document_id in self.ranked[:k]
            if document_id not in self.relevant
        )

    def missing(self) -> tuple[str, ...]:
        """Relevant ids the engine never returned within the ranked list."""
        return tuple(sorted(self.relevant - set(self.ranked)))

    def relevance_margin(self) -> float | None:
        """Best relevant score minus best non-relevant score.

        Positive means *every* relevant document outranks *every*
        non-relevant one, which is the property Precision@K cannot show
        once the metrics saturate. ``None`` when one of the two groups is
        absent from the ranked list (or scores were not captured).
        """
        if not self.scores or len(self.scores) != len(self.ranked):
            return None
        relevant_scores = [
            score
            for document_id, score in zip(self.ranked, self.scores, strict=True)
            if document_id in self.relevant
        ]
        other_scores = [
            score
            for document_id, score in zip(self.ranked, self.scores, strict=True)
            if document_id not in self.relevant
        ]
        if not relevant_scores or not other_scores:
            return None
        return max(relevant_scores) - max(other_scores)

    def top_spread(self) -> float:
        """Gap between the first and the last relevant result."""
        relevant_scores = [
            score
            for document_id, score in zip(self.ranked, self.scores, strict=True)
            if document_id in self.relevant
        ]
        if len(relevant_scores) < 2:
            return 0.0
        return relevant_scores[0] - relevant_scores[-1]

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "relevant": sorted(self.relevant),
            "ranked": list(self.ranked),
            "scores": list(self.scores),
            "points": [
                {name: round(value, 4) for name, value in breakdown.items()}
                for breakdown in self.points
            ],
            "precision_at_k": {str(k): v for k, v in self.precision.items()},
            "recall_at_k": {str(k): v for k, v in self.recall.items()},
            "reciprocal_rank": self.reciprocal_rank,
            "first_relevant_rank": self.first_relevant,
            "relevance_margin": self.relevance_margin(),
            "top_spread": self.top_spread(),
            "intruders_at_3": list(self.intruders(3)),
            "missing": list(self.missing()),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Aggregate of every :class:`QueryScore` in one run."""

    scores: tuple[QueryScore, ...]
    k_values: tuple[int, ...] = DEFAULT_K_VALUES

    def mean_precision(self, k: int) -> float:
        if not self.scores:
            return 0.0
        return sum(score.precision.get(k, 0.0) for score in self.scores) / len(
            self.scores
        )

    def mean_recall(self, k: int) -> float:
        if not self.scores:
            return 0.0
        return sum(score.recall.get(k, 0.0) for score in self.scores) / len(
            self.scores
        )

    def mrr(self) -> float:
        if not self.scores:
            return 0.0
        return sum(score.reciprocal_rank for score in self.scores) / len(
            self.scores
        )

    def by_query(self) -> dict[str, QueryScore]:
        return {score.query: score for score in self.scores}

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": len(self.scores),
            "k_values": list(self.k_values),
            "mean_precision_at_k": {
                str(k): self.mean_precision(k) for k in self.k_values
            },
            "mean_recall_at_k": {
                str(k): self.mean_recall(k) for k in self.k_values
            },
            "mrr": self.mrr(),
            "per_query": [score.as_dict() for score in self.scores],
        }


def score_query(
    query: str,
    ranked: Sequence[str],
    relevant: Collection[str],
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    scores: Sequence[float] = (),
    points: Sequence[dict[str, float]] = (),
) -> QueryScore:
    """Measure one ranked list against its relevance labels.

    ``scores`` are the engine's composite scores in the same order, and
    ``points`` the per-signal weighted breakdown the engine produces with
    ``explain=True``; both are optional and only feed the diagnostics.
    """
    return QueryScore(
        query=query,
        relevant=frozenset(relevant),
        ranked=tuple(ranked),
        precision={k: precision_at_k(ranked, relevant, k) for k in k_values},
        recall={k: recall_at_k(ranked, relevant, k) for k in k_values},
        reciprocal_rank=reciprocal_rank(ranked, relevant),
        first_relevant=first_relevant_rank(ranked, relevant),
        scores=tuple(scores),
        points=tuple(dict(breakdown) for breakdown in points),
    )


def evaluate(
    measured: Sequence[
        tuple[str, Sequence[str], frozenset[str], Sequence[float],
               Sequence[dict[str, float]]]
    ],
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> EvaluationReport:
    """Build an :class:`EvaluationReport` from
    (query, ranked, relevant, scores, points) tuples."""
    return EvaluationReport(
        scores=tuple(
            score_query(query, ranked, relevant, k_values, scores, points)
            for query, ranked, relevant, scores, points in measured
        ),
        k_values=tuple(k_values),
    )


# -- phase 045: correctness metrics a relevance metric cannot see ---------------


def filter_accuracy(
    cases: Sequence[tuple[str, bool]], *, unlabelled: Sequence[str] = ()
) -> dict[str, object]:
    """How often a filtered query returned only results satisfying the filter.

    ``cases`` is ``(query, every_result_satisfied_the_filter)`` per query that
    carried at least one filter. ``unlabelled`` names filtered queries the
    caller could not evaluate, which are reported separately rather than
    counted as passes: a metric that silently drops the queries it cannot
    judge is a metric that reports 1.0 because it looked at nothing.

    A relevance metric genuinely cannot see this failure. ``type:pdf`` with a
    perfect P@5 is still wrong if the fifth result is a text file, and the only
    thing that notices is a predicate that asks the question directly.
    """
    if not cases:
        return {
            "queries": 0,
            "correct": 0,
            "accuracy": 1.0,
            "unlabelled": list(unlabelled),
            "wrong": [],
        }
    wrong = [query for query, ok in cases if not ok]
    correct = len(cases) - len(wrong)
    return {
        "queries": len(cases),
        "correct": correct,
        "accuracy": correct / len(cases),
        "unlabelled": list(unlabelled),
        "wrong": wrong,
    }


def zero_result_accuracy(
    cases: Sequence[tuple[str, bool]], *, unlabelled: Sequence[str] = ()
) -> dict[str, object]:
    """How often a "must return nothing" query actually returned nothing.

    Distinct from the empty-relevance branch inside :func:`precision_at_k`,
    which rewards silence as a side effect of scoring one query. This asks the
    question on its own terms and aggregates it, so the answer survives a
    corpus that happens to contain one such query -- which, until phase 045, it
    did.
    """
    if not cases:
        return {
            "queries": 0,
            "silent": 0,
            "accuracy": 1.0,
            "unlabelled": list(unlabelled),
            "noisy": [],
        }
    noisy = [query for query, silent in cases if not silent]
    silent = len(cases) - len(noisy)
    return {
        "queries": len(cases),
        "silent": silent,
        "accuracy": silent / len(cases),
        "unlabelled": list(unlabelled),
        "noisy": noisy,
    }
