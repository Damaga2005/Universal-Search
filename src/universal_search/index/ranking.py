"""Deterministic composite ranking for search results.

Formula
-------
    score = (sum(weight_i * signal_i)) / (sum(weight_i))

Every signal is normalized to [0, 1]. With the default weights the
denominator is 14.0, so a score is always within [0, 1] and comparable
across queries.

Signals and default weights (see docs/RANKING.md for the rationale):

    filename_exact   3.0  query equals the file name or its stem
    filename_tokens  2.0  fraction of query terms present in the name
    phrase_exact     2.0  query terms appear adjacent in the content
    term_freq        1.5  term frequency in the content (saturates at 10)
    proximity        1.5  tightest window containing all terms
    bm25             2.0  FTS5 relevance, monotically normalized
    path_match       0.8  terms in parent directories (terms of length >= 2)
    doc_type         0.5  extractable content (1.0) vs metadata-only (0.4)
    source           0.4  provider/context weight (extension point)
    recency          0.3  secondary signal, bounded to [0.5, 1.0]
    usage            0.0  reserved for future local usage signals

No personalization, learning or network service exists in this module.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from universal_search.index.database import SearchDatabase  # noqa: F401  (re-export guard)


WORD_RE = re.compile(r"\w+", re.UNICODE)

# Term frequency saturates at this many occurrences (a term used 10+ times
# scores the same as one used 1000 times).
FREQ_SATURATION = 10.0

# Recency: half-life style decay over this many days, floored at 0.5 so an
# old document can never be buried by age alone.
RECENCY_DECAY_DAYS = 180.0
RECENCY_FLOOR = 0.5
NEUTRAL_RECENCY = 0.75

SOURCE_SCORES = {"local": 1.0, "onedrive": 1.0, "other": 1.0}


def tokens(text: str) -> list[str]:
    """Word tokens, splitting on underscores like FTS5's unicode61 tokenizer."""
    return WORD_RE.findall(text.replace("_", " "))


def query_terms(query: str) -> tuple[str, ...]:
    """Case-folded word terms of a free-text query, duplicates preserved."""
    return tuple(tokens(query.casefold()))


@dataclass(frozen=True, slots=True)
class RankingWeights:
    filename_exact: float = 3.0
    filename_tokens: float = 2.0
    phrase_exact: float = 2.0
    term_freq: float = 1.5
    proximity: float = 1.5
    bm25: float = 2.0
    path_match: float = 0.8
    doc_type: float = 0.5
    source: float = 0.4
    recency: float = 0.3
    usage: float = 0.0  # reserved; usage signals are never collected

    @property
    def total(self) -> float:
        return sum(getattr(self, field.name) for field in fields(self))


DEFAULT_WEIGHTS = RankingWeights()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One result candidate produced by the storage layer."""

    name: str
    path: str
    content: str | None
    source: str
    modified_at: str | None
    bm25_rank: float


class Ranker:
    def __init__(self, weights: RankingWeights = DEFAULT_WEIGHTS) -> None:
        self.weights = weights

    def signals(
        self,
        candidate: Candidate,
        terms: tuple[str, ...],
        *,
        usage_boost: float = 0.0,
        now: datetime | None = None,
    ) -> dict[str, float]:
        """Compute each normalized signal for one candidate."""
        unique = tuple(dict.fromkeys(terms))
        name = candidate.name.casefold()
        stem = name.rsplit(".", 1)[0] if "." in name else name
        phrase = " ".join(unique)
        name_phrase = " ".join(tokens(name))
        stem_phrase = " ".join(tokens(stem))
        content = candidate.content or ""
        content_cf = content.casefold()
        words = tokens(content_cf)
        word_set = set(words)
        name_tokens = set(tokens(name))

        signals: dict[str, float] = {}

        # 1. exact filename match (strongest single signal)
        signals["filename_exact"] = (
            1.0 if unique and phrase in {name_phrase, stem_phrase} else 0.0
        )

        # 2. filename token match
        signals["filename_tokens"] = (
            sum(term in name_tokens for term in unique) / len(unique)
            if unique
            else 0.0
        )

        # 3. path match — parent directories only, single-character terms are
        #    excluded so drive letters can never dominate.
        components = re.split(r"[\\/]", candidate.path)[:-1]
        component_tokens: set[str] = set()
        for component in components:
            component_tokens.update(tokens(component.casefold()))
        eligible = [term for term in unique if len(term) >= 2]
        signals["path_match"] = (
            sum(term in component_tokens for term in eligible) / len(eligible)
            if eligible
            else 0.0
        )

        # 4. exact phrase match in content
        if not unique:
            signals["phrase_exact"] = 0.0
        elif len(unique) == 1:
            signals["phrase_exact"] = 1.0 if unique[0] in word_set else 0.0
        else:
            signals["phrase_exact"] = (
                1.0 if " ".join(unique) in " ".join(words) else 0.0
            )

        # 5. term frequency, saturating
        counts = Counter(word for word in words if word in set(unique))
        signals["term_freq"] = (
            sum(
                min(counts.get(term, 0) / FREQ_SATURATION, 1.0)
                for term in unique
            )
            / len(unique)
        ) if unique else 0.0

        # 6. proximity: tightest window covering every term
        signals["proximity"] = self._proximity(unique, words)

        # 7. BM25 relevance, mapped monotonically into [0, 1)
        positive = max(0.0, -candidate.bm25_rank)
        signals["bm25"] = positive / (1.0 + positive)

        # 8. document type: content-bearing vs metadata-only
        signals["doc_type"] = 1.0 if content else 0.4

        # 9. source/context (extension point for workspace weighting)
        signals["source"] = SOURCE_SCORES.get(candidate.source, 1.0)

        # 10. recency, secondary and bounded
        signals["recency"] = self._recency(candidate.modified_at, now)

        # reserved extension point: local usage signals (never collected yet)
        signals["usage"] = min(max(usage_boost, 0.0), 1.0)
        return signals

    @staticmethod
    def _proximity(terms: tuple[str, ...], words: list[str]) -> float:
        if not terms or not words:
            return 0.0
        positions: dict[str, list[int]] = {}
        for index, word in enumerate(words):
            if word in terms:
                positions.setdefault(word, []).append(index)
        lists = [positions.get(term) for term in terms]
        if any(not item for item in lists):
            return 0.0
        if len(terms) == 1:
            return 1.0
        heads = [0] * len(lists)
        best_span = math.inf
        while True:
            current = [item[heads[i]] for i, item in enumerate(lists)]
            best_span = min(best_span, max(current) - min(current) + 1)
            lowest = min(range(len(current)), key=current.__getitem__)
            heads[lowest] += 1
            if heads[lowest] >= len(lists[lowest]):
                break
        return min(len(terms) / best_span, 1.0)

    @staticmethod
    def _recency(modified_at: str | None, now: datetime | None) -> float:
        if not modified_at:
            return NEUTRAL_RECENCY
        try:
            modified = datetime.fromisoformat(modified_at)
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            reference = now or datetime.now(timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            age_days = max((reference - modified).days, 0)
        except ValueError:
            return NEUTRAL_RECENCY
        return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * math.exp(
            -age_days / RECENCY_DECAY_DAYS
        )

    def score(
        self,
        candidate: Candidate,
        terms: tuple[str, ...],
        *,
        usage_boost: float = 0.0,
        now: datetime | None = None,
    ) -> float:
        signals = self.signals(
            candidate, terms, usage_boost=usage_boost, now=now
        )
        weights = self.weights
        numerator = sum(
            getattr(weights, name) * value for name, value in signals.items()
        )
        return numerator / weights.total
