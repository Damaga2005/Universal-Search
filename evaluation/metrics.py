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
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field


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
    k_values: tuple[int, ...] = (1, 3, 5)

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
    k_values: Sequence[int] = (1, 3, 5),
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
    k_values: Sequence[int] = (1, 3, 5),
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
