"""Phase 044: bounded, query-scoped, decaying local learning.

Everything in this module is a pure function over numbers, because a ranking
signal that cannot be evaluated on its own cannot be argued about. The
repository and the SQL live in ``index/search.py``; this file says what a set of
usage events is *worth*.

Three decisions, and the measurement behind each one.

**1. The signal is scoped to the query it was learned from.**
Before phase 044 the whole signal was one number: how many times a document had
been opened, ever, under any query. A document opened for ``examen`` was
therefore also boosted for ``receta paella``. The ``query`` column had been
recorded since phase 008 and read by exactly one query -- the one the user
inspects. Learning that cannot tell one task from another does not improve a
repeated workflow; it just adds noise. So an event counts for the query it was
recorded under, and a much smaller share counts for the document in general.

**2. Old events fade.**
``opened_at`` was in the schema and unused by ranking, so an event from 2024
still carried full weight. There is no cap on the table and no expiry, so the
only thing preventing "learning permanently buries new documents" was the small
weight -- which is a promise rather than a mechanism. Events now decay, and the
decay is a four-bucket step rather than an exponential, because a step can be
read on a screen and argued about.

**3. The weight is measured, not chosen.**
``evaluation/learning_gate.py`` reports the largest share of the final score
that learning can take, and compares it against the share ``recency`` -- the
signal the project already documents as the bounded, secondary one. That
comparison is the argument.

The floors matter as much as the weights. ``MIN_EFFECTIVE_EVENTS`` means a
single accidental open changes nothing, which is what makes cold start
*deterministic* rather than merely quiet: with no events at all, or one, the
answer is the baseline's answer, byte for byte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# -- the decay ----------------------------------------------------------------
# Four buckets, and the reason it is a step and not a curve: a curve produces a
# number nobody can look at and argue with. The values are the share of an
# event that survives, at the *oldest* age of its bucket.
#
# 30 days / 90 days / 365 days are the same order of magnitude as
# `RECENCY_DECAY_DAYS` in the ranker, so a document's own age and the age of
# what you did with it decay on a comparable clock.
DECAY_BUCKETS: tuple[tuple[int, float], ...] = (
    (30, 1.0),
    (90, 0.6),
    (365, 0.3),
)
DECAY_FLOOR = 0.1
# Older than the last bucket: this much, forever. Never zero, because "you
# opened this once two years ago" is evidence, just old evidence, and a signal
# that can drop to nothing cannot distinguish "never" from "long ago".
UNBOUNDED_AGE_DAYS = 10_000

# -- the two shares -----------------------------------------------------------
# How much of the boost comes from opens under *this* query, and how much from
# opens of that document under any query at all.
#
# The general share is deliberately small. It exists so that a document you keep
# opening stays findable when you phrase the request differently, and it is the
# number that must never grow into something that overrides what you typed.
QUERY_SHARE = 0.8
GENERAL_SHARE = 0.2

# Saturation, unchanged from phase 008: four effective opens is "enough". Raising
# it would not add evidence, it would add delay.
USAGE_SATURATION_OPENS = 4.0

# Below this many *effective* events, learning contributes exactly nothing.
# One accidental open must not reorder anybody's results.
MIN_EFFECTIVE_EVENTS = 2.0

# Kept for the callers that already imported it from `context`, and for the
# phase-008 test that pins its values. The old single-argument behaviour is
# still correct: no age means no decay.
def usage_boost_from(open_count: int, age_days: float = 0.0) -> float:
    """A saturating boost for a run of opens, decayed by age.

    The same numbers as phase 008 when ``age_days`` is 0 -- 1 open is 0.43, 4
    is 1.0, a thousand is still 1.0 -- and strictly less for anything older.
    """
    if open_count <= 0:
        return 0.0
    decayed = _decay(age_days) * float(open_count)
    if decayed < MIN_EFFECTIVE_EVENTS:
        return 0.0
    return min(1.0, math.log1p(decayed) / math.log1p(USAGE_SATURATION_OPENS))


def _decay(age_days: float) -> float:
    """How much of an event that old is still worth."""
    try:
        age = float(age_days)
    except (TypeError, ValueError):
        return DECAY_FLOOR
    if age < 0:
        return 1.0  # a clock that disagrees is not evidence of age
    for limit, share in DECAY_BUCKETS:
        if age <= limit:
            return share
    return DECAY_FLOOR


@dataclass(frozen=True, slots=True)
class UsageSignal:
    """What the learned events are worth for one document, right now."""

    document_id: str
    query_events: float = 0.0
    global_events: float = 0.0
    query: str = ""

    @property
    def effective(self) -> float:
        return self.query_events * QUERY_SHARE + self.global_events * GENERAL_SHARE

    @property
    def boost(self) -> float:
        """The number the ranker consumes, always in [0, 1]."""
        if self.effective < MIN_EFFECTIVE_EVENTS:
            return 0.0
        value = min(1.0, math.log1p(self.effective) / math.log1p(USAGE_SATURATION_OPENS))
        return min(1.0, value)

    @property
    def is_inert(self) -> bool:
        """Whether this document's learning changes nothing.

        Cold start, one accidental open, and a learning system that has just
        been switched on all land here, and all three must produce the
        baseline's answer exactly.
        """
        return self.boost == 0.0

    def note(self) -> str:
        """A sentence a person can read in the explanation.

        Phase 044's contract requires a material learning-driven change to be
        explainable. Before this existed, ``explain_notes`` had entries for the
        personal context and none at all for usage -- the one signal that
        silently moved results around.
        """
        if self.is_inert:
            return ""
        parts = []
        if self.query_events:
            parts.append(
                f"{_count(self.query_events)} apertura(s) para «{self.query}»"
            )
        if self.global_events:
            parts.append(f"{_count(self.global_events)} apertura(s) en total")
        return "uso local: " + " y ".join(parts)


def _count(value: float) -> str:
    rounded = int(round(value))
    return str(rounded)


def normalise_query(query: str) -> str:
    """The form of a query that two spellings of the same request share.

    Lower case and collapsed whitespace, and nothing else: stripping accents or
    punctuation would make two different requests compare equal, which is a
    worse failure than a missed boost.
    """
    return " ".join(str(query or "").split()).casefold()


def signal_for(
    document_id: str,
    events,
    *,
    query: str = "",
) -> UsageSignal:
    """Fold one document's events into a :class:`UsageSignal`.

    ``events`` is an iterable of ``(query_text, age_days)`` pairs. Splitting by
    query and folding by age happens here, in one place, so the SQL layer only
    has to hand over what it read.
    """
    wanted = normalise_query(query)
    query_events = 0.0
    global_events = 0.0
    for text, age_days in events:
        share = _decay(age_days)
        global_events += share
        if wanted and normalise_query(text) == wanted:
            query_events += share
    return UsageSignal(
        document_id=document_id,
        query_events=query_events,
        global_events=global_events,
        query=query.strip(),
    )


def signals_by_document(events_by_document, *, query: str = "") -> dict[str, UsageSignal]:
    """Every document's :class:`UsageSignal`, keyed by document id.

    Documents with no events are simply absent, so a caller iterating the
    result set treats "no signal" and "zero signal" identically -- which is the
    cold-start behaviour, arrived at without a special case.
    """
    return {
        document_id: signal_for(document_id, events, query=query)
        for document_id, events in events_by_document.items()
        if events
    }


def summary(signals: dict[str, UsageSignal]) -> dict[str, float]:
    """Counts for the explanation and for the gate: what learning is doing."""
    return {
        "documents": float(len(signals)),
        "with_effect": float(sum(1 for s in signals.values() if not s.is_inert)),
        "boost_sum": round(sum(s.boost for s in signals.values()), 6),
        "boost_max": round(
            max((s.boost for s in signals.values()), default=0.0), 6
        ),
    }