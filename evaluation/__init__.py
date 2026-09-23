"""Ranking-quality evaluation with labelled queries (spec 013).

Separate from the unit-test suite on purpose: this is the measurement
instrument used to justify ranking changes, and it is meant to be run by
hand as often as the weights are touched.

    python -m evaluation
    python -m evaluation --top 5 --json results.json
    python -m evaluation --write-fixture evaluation/baseline.json

Everything is local and deterministic: a synthetic labelled corpus, the
real search engine, SQLite, and Precision@K / Recall@K / MRR. No network,
no service, no external API.
"""

from evaluation.corpus import (
    DOCUMENT_IDS,
    DOCUMENTS,
    LABELLED_QUERIES,
    CorpusDocument,
    LabelledQuery,
)
from evaluation.metrics import (
    EvaluationReport,
    QueryScore,
    evaluate,
    first_relevant_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_query,
)

__all__ = [
    "DOCUMENTS",
    "DOCUMENT_IDS",
    "LABELLED_QUERIES",
    "CorpusDocument",
    "EvaluationReport",
    "LabelledQuery",
    "QueryScore",
    "evaluate",
    "first_relevant_rank",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_query",
]
