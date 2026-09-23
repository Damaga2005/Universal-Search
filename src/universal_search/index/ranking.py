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
    usage            0.0  local usage learning (0.0 until the user enables it)
    context          0.0  bounded personal-context boost (0.0 unless active)

When local usage learning or a personal context is active its weight is
raised to ``ACTIVATED_USAGE_WEIGHT`` / ``ACTIVATED_CONTEXT_WEIGHT`` and the
denominator grows accordingly (scores stay normalized to [0, 1]). The boosts
are bounded inputs computed by :mod:`universal_search.context`: this module
stores no personal data, learns nothing and performs no network access.
"""

import hashlib
import math
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from functools import lru_cache


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


# Bounded cache behind content_words(): keyed by SHA-256 of the content, so
# a hit is byte-identical text - never a stale approximation. Capped
# wholesale, trading hit rate for a fixed memory ceiling.
_CONTENT_WORDS: dict[bytes, tuple[tuple[str, ...], frozenset[str]]] = {}
_CONTENT_WORDS_CAP = 1024


def content_words(content: str) -> tuple[tuple[str, ...], frozenset[str]]:
    """Case-folded content tokens plus their set, shared across queries.

    One search per keystroke re-scores the same candidate pool, and
    tokenizing identical text again is pure waste (profiled: the dominant
    per-query cost). Both values feed the word-based signals below; callers
    must treat them as read-only.
    """
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    cached = _CONTENT_WORDS.get(digest)
    if cached is None:
        words = tuple(tokens(content.casefold()))
        cached = (words, frozenset(words))
        if len(_CONTENT_WORDS) >= _CONTENT_WORDS_CAP:
            _CONTENT_WORDS.clear()
        _CONTENT_WORDS[digest] = cached
    return cached


@lru_cache(maxsize=4096)
def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp once per distinct string.

    Every keystroke re-scores the same candidate rows, and
    ``datetime.fromisoformat`` per candidate per query was measurable
    overhead. Returns ``None`` for anything invalid, which callers treat
    as neutral recency (same as the old inline ``except ValueError``).
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@lru_cache(maxsize=4096)
def _name_parts(name: str) -> tuple[tuple[str, ...], str, str]:
    """(name tokens, name phrase, stem phrase) once per distinct filename.

    Candidates recur on every keystroke; re-tokenizing the name and its
    stem for each of them was measurable overhead in the per-candidate
    hot path. ``name`` arrives casefolded, as the caller did before.
    """
    name_toks = tokens(name)
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return name_toks, " ".join(name_toks), " ".join(tokens(stem))


@lru_cache(maxsize=4096)
def _path_component_tokens(path: str) -> frozenset[str]:
    """Tokens of ``path``'s parent directories, once per distinct path."""
    component_tokens: set[str] = set()
    for component in re.split(r"[\\/]", path)[:-1]:
        component_tokens.update(tokens(component.casefold()))
    return frozenset(component_tokens)


def clear_caches() -> None:
    """Explicit invalidation for every ranking cache (spec 011).

    Normal operation never needs this: all four caches are keyed by
    value (content digest, exact strings), so a hit is byte-identical
    and eviction is a pure memory decision. Rebuild tooling and tests
    use this to start from a cold, provably empty state.
    """
    _CONTENT_WORDS.clear()
    _parse_iso.cache_clear()
    _name_parts.cache_clear()
    _path_component_tokens.cache_clear()


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
    usage: float = 0.0    # local usage learning: 0.0 until enabled (then 0.5)
    context: float = 0.0  # personal context boost: 0.0 unless applied (then 1.0)

    @property
    def total(self) -> float:
        return sum(getattr(self, field.name) for field in fields(self))


DEFAULT_WEIGHTS = RankingWeights()

# Raised only when the corresponding optional, user-enabled signal takes part
# (phase 008). The defaults above keep both at 0.0.
ACTIVATED_USAGE_WEIGHT = 0.5
ACTIVATED_CONTEXT_WEIGHT = 1.0


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
        context_boost: float = 0.0,
        now: datetime | None = None,
    ) -> dict[str, float]:
        """Compute each normalized signal for one candidate."""
        unique = tuple(dict.fromkeys(terms))
        unique_set = set(unique)
        name = candidate.name.casefold()
        phrase = " ".join(unique)
        name_toks, name_phrase, stem_phrase = _name_parts(name)
        content = candidate.content or ""
        words, word_set = content_words(content)
        name_tokens = set(name_toks)

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
        component_tokens = _path_component_tokens(candidate.path)
        eligible = [term for term in unique if len(term) >= 2]
        signals["path_match"] = (
            sum(term in component_tokens for term in eligible) / len(eligible)
            if eligible
            else 0.0
        )

        # 4/5/6. One pass over the content tokens feeds the phrase check,
        #    the per-term counts and the positions for proximity — three
        #    former scans merged into one. This is the per-candidate hot
        #    path: every keystroke re-runs it for the whole pool.
        span = len(unique)
        first = unique[0] if unique else ""
        counts: dict[str, int] = {}
        positions: dict[str, list[int]] = {}
        adjacent = False
        for index, word in enumerate(words):
            if word in unique_set:
                counts[word] = counts.get(word, 0) + 1
                positions.setdefault(word, []).append(index)
                if (
                    span > 1
                    and word == first
                    and words[index:index + span] == unique
                ):
                    adjacent = True

        # 4. exact phrase in content: the query terms must appear as
        #    adjacent tokens; token boundaries are respected, so a
        #    substring inside a longer word can never fake a phrase.
        if not unique:
            signals["phrase_exact"] = 0.0
        elif span == 1:
            signals["phrase_exact"] = 1.0 if unique[0] in word_set else 0.0
        else:
            signals["phrase_exact"] = 1.0 if adjacent else 0.0

        # 5. term frequency, saturating
        signals["term_freq"] = (
            sum(
                min(counts.get(term, 0) / FREQ_SATURATION, 1.0)
                for term in unique
            )
            / len(unique)
        ) if unique else 0.0

        # 6. proximity: tightest window covering every term
        signals["proximity"] = self._proximity(unique, positions)

        # 7. BM25 relevance, mapped monotonically into [0, 1)
        positive = max(0.0, -candidate.bm25_rank)
        signals["bm25"] = positive / (1.0 + positive)

        # 8. document type: content-bearing vs metadata-only
        signals["doc_type"] = 1.0 if content else 0.4

        # 9. source/context (extension point for workspace weighting)
        signals["source"] = SOURCE_SCORES.get(candidate.source, 1.0)

        # 10. recency, secondary and bounded
        signals["recency"] = self._recency(candidate.modified_at, now)

        # Optional bounded extension points (both off unless activated):
        # local usage learning and the personal context preference.
        signals["usage"] = min(max(usage_boost, 0.0), 1.0)
        signals["context"] = min(max(context_boost, 0.0), 1.0)
        return signals

    @staticmethod
    def _proximity(
        terms: tuple[str, ...], positions: dict[str, list[int]]
    ) -> float:
        """Tightest window covering every term, from precomputed positions.

        ``positions`` comes from the fused pass in :meth:`signals`; a term
        with no occurrence yields 0.0, exactly as when the positions were
        rebuilt here from the word list.
        """
        if not terms:
            return 0.0
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
        modified = _parse_iso(modified_at)
        if modified is None:
            return NEUTRAL_RECENCY
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age_days = max((reference - modified).days, 0)
        return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * math.exp(
            -age_days / RECENCY_DECAY_DAYS
        )

    def contributions(
        self,
        candidate: Candidate,
        terms: tuple[str, ...],
        *,
        usage_boost: float = 0.0,
        context_boost: float = 0.0,
        now: datetime | None = None,
    ) -> tuple[dict[str, float], float]:
        """Weighted contribution of every signal plus the final score.

        The breakdown is what makes personalization explainable: each point
        of score is attributable to a named signal.
        """
        signals = self.signals(
            candidate,
            terms,
            usage_boost=usage_boost,
            context_boost=context_boost,
            now=now,
        )
        weights = self.weights
        points = {
            name: getattr(weights, name) * value for name, value in signals.items()
        }
        return points, sum(points.values()) / weights.total

    def score(
        self,
        candidate: Candidate,
        terms: tuple[str, ...],
        *,
        usage_boost: float = 0.0,
        context_boost: float = 0.0,
        now: datetime | None = None,
    ) -> float:
        """Final score for ``candidate`` — fast path without explain dict.

        Numerically identical to ``contributions(...)[1]``: it only skips
        the per-signal points dict that the explain path requires.
        """
        signals = self.signals(
            candidate,
            terms,
            usage_boost=usage_boost,
            context_boost=context_boost,
            now=now,
        )
        weights = self.weights
        return sum(
            getattr(weights, name) * value for name, value in signals.items()
        ) / weights.total
