"""Phase 044: measuring whether local learning earns its place.

Everything here is synthetic and local. There is no profile, no embedding and no
network call; a "user" is a list of events written by ``build_history``, and the
point of the module is that the whole thing is reproducible from a seed.

What it measures, in the order the prompt asks:

* **cold start** — a new user must get the baseline's answer, byte for byte;
* **determinism** — the same history twice gives the same order;
* **forgetting** — an old history must decay below a recent one;
* **bounded** — the largest share of the final score learning can take, against
  the share ``recency`` already holds;
* **exact-match protection** — an exact filename still beats a learned document;
* **benefit** — on a repeated workflow, does learning actually help?
* **regression** — on the fixed corpus with no history, nothing moves at all.

The last two are the ones that decide whether anything ships. A learning signal
that is bounded and explainable and does not help is still not worth having.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from universal_search import learn
from universal_search.index.ranking import (
    ACTIVATED_USAGE_WEIGHT,
    DEFAULT_WEIGHTS,
    RankingWeights,
)
from universal_search.index.search import SearchEngine, SearchResult

# A fixed instant, so every measurement in this module is reproducible. The
# corpus's own `BASE_EPOCH` exists for the same reason.
MEASUREMENT_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One thing a user opened, and for which query, and when."""

    document: str
    query: str
    age_days: float = 0.0

    @property
    def opened_at(self) -> str:
        return (
            MEASUREMENT_NOW - timedelta(days=self.age_days)
        ).isoformat(timespec="seconds")


def build_history(events) -> list[UsageEvent]:
    """A synthetic interaction history, in the order it happened.

    A list rather than a generator on purpose: a caller that wants to compare
    two histories should be able to write them down and read them back.
    """
    return list(events)


def repeated_opens(document: str, query: str, *, times: int, age_days: float = 0.0):
    """The same task done ``times``: the workflow learning exists to serve."""
    return [
        UsageEvent(document=document, query=query, age_days=age_days)
        for _ in range(times)
    ]


def record(engine: SearchEngine, history) -> int:
    """Write a synthetic history into the engine's own database."""
    recorded = 0
    for event in history:
        with engine.database.connect() as connection:
            connection.execute(
                "INSERT INTO usage_events (document_id, query, opened_at) "
                "VALUES (?, ?, ?)",
                (event.document, event.query, event.opened_at),
            )
            connection.commit()
        recorded += 1
    return recorded


def scores(results: list[SearchResult]) -> dict[str, float]:
    """Every result's final score, keyed by name, rounded to twelve places.

    Twelve places rather than ``repr`` so that two runs compare as equal when
    they are equal, and differ when they differ, without a floating-point
    representation getting in the way of the comparison.
    """
    return {result.name: round(result.score, 12) for result in results}


def order(results: list[SearchResult]) -> list[str]:
    return [result.name for result in results]


def mean_reciprocal_rank(engine: SearchEngine, labelled_queries, *, usage: bool) -> float:
    """MRR over the labelled corpus, the same number ``evaluation.gate`` reads.

    Imported from the project's own metrics rather than recomputed: if the gate
    and this measurement disagreed about MRR, the phase would be arguing with
    the harness instead of with itself.
    """
    from evaluation.metrics import reciprocal_rank

    total = 0.0
    for labelled in labelled_queries:
        results = engine.search(
            labelled.query, limit=10, usage=usage, now=MEASUREMENT_NOW
        )
        total += reciprocal_rank(order(results), labelled.relevant)
    return total / len(labelled_queries) if labelled_queries else 0.0


def usage_effect() -> float:
    """The largest amount of final score learning can add to one document.

    Structural, from the weights: a saturated boost is 1.0 and the score is a
    weighted mean, so the ceiling is ``usage / (total + usage)``. Everything
    the contract claims about learning is a claim about how small this is
    relative to the margins explicit signals decide.
    """
    total = DEFAULT_WEIGHTS.total
    return round(ACTIVATED_USAGE_WEIGHT / (total + ACTIVATED_USAGE_WEIGHT), 6)


def narrowest_exact_margin(exact_probe) -> float:
    """The smallest lead an exact-name match holds over its best rival.

    ``exact_probe`` yields ``(margin, description)`` pairs; the narrowest one is
    the number learning would have to exceed to overturn an exact match. This is
    the empirical half of the contract -- the ranking module promises exact
    matches are never *boosted*, and this measures how much room a boost would
    still have if it were.
    """
    margins = list(exact_probe)
    if not margins:
        return float("inf")
    return round(min(margin for margin, _ in margins), 6)


def promotion_sweep(engine: SearchEngine, labelled_queries, *, times: int = 6):
    """Every (query, candidate) pair, given that candidate a repeated history.

    The honest measurement of benefit: not "does learning do something" but
    "how often, and how far". Every pair is tried, none is chosen, and the
    report keeps the ones that moved in either direction.

    One engine is reused and the events are deleted between pairs, so the
    sweep measures the signal rather than the disk.

    Events carry the database id, not the file name. That distinction is not
    pedantry: the first version of this sweep wrote names into
    ``usage_events``, matched nothing, and reported that learning never helped
    anything -- a clean, confident, completely wrong zero.
    """
    ids_by_name = document_ids(engine)
    improved: list[tuple[str, str, int, int]] = []
    demoted: list[tuple[str, str, int, int]] = []
    already_first = 0
    considered = 0
    for labelled in labelled_queries:
        baseline = order(
            engine.search(labelled.query, limit=10, usage=False, now=MEASUREMENT_NOW)
        )
        if len(baseline) < 2:
            continue
        for candidate in baseline:
            considered += 1
            _clear_events(engine)
            _insert_events(
                engine,
                _repeated(ids_by_name[candidate], labelled.query, times, 1.0),
            )
            learned = order(
                engine.search(labelled.query, limit=10, usage=True,
                              now=MEASUREMENT_NOW)
            )
            before = baseline.index(candidate)
            after = learned.index(candidate) if candidate in learned else -1
            if before == 0:
                already_first += 1
            if 0 <= after < before:
                improved.append((labelled.query, candidate, before, after))
            elif after > before:
                demoted.append((labelled.query, candidate, before, after))
    _clear_events(engine)
    return {
        "considered": considered,
        "already_first": already_first,
        "improved": improved,
        "demoted": demoted,
    }


def document_ids(engine: SearchEngine) -> dict[str, str]:
    """``{file name: database id}`` for every indexed document."""
    with engine.database.connect() as connection:
        rows = connection.execute("SELECT id, name FROM documents").fetchall()
    return {row["name"]: row["id"] for row in rows}


def _repeated(document: str, query: str, times: int, age_days: float):
    return [(document, query, age_days)] * times


def _clear_events(engine: SearchEngine) -> None:
    with engine.database.connect() as connection:
        connection.execute("DELETE FROM usage_events")
        connection.commit()


def _insert_events(engine: SearchEngine, events) -> None:
    stamp = (MEASUREMENT_NOW - timedelta(days=events[0][2])).isoformat(
        timespec="seconds"
    )
    with engine.database.connect() as connection:
        for document, query, _ in events:
            connection.execute(
                "INSERT INTO usage_events (document_id, query, opened_at) "
                "VALUES (?, ?, ?)",
                (document, query, stamp),
            )
        connection.commit()


def document_id(engine: SearchEngine, path_fragment: str) -> str:
    """The database id of the indexed document whose path ends with a fragment.

    Corpus ids are not database ids: the indexer keys documents by the hash of
    their path, so a gate that wrote a synthetic history under a corpus id would
    be recording events against nothing.
    """
    with engine.database.connect() as connection:
        rows = connection.execute(
            "SELECT id, path FROM documents WHERE path LIKE ?",
            (f"%{path_fragment}",),
        ).fetchall()
    if not rows:
        raise LookupError(f"no indexed document under {path_fragment!r}")
    if len(rows) > 1:
        raise LookupError(f"{path_fragment!r} matches {len(rows)} documents")
    return rows[0]["id"]


def exact_name_candidates(engine: SearchEngine) -> list[tuple[str, float]]:
    """Every indexed document whose stem equals its file name minus extension.

    The corpus is written so that exact-name matches are rare and deliberate;
    this is what the dominance measurement is run against.
    """
    with engine.database.connect() as connection:
        rows = connection.execute(
            "SELECT id, name, path FROM documents ORDER BY path"
        ).fetchall()
    exact = []
    for row in rows:
        stem = str(row["name"]).rsplit(".", 1)[0]
        if stem and " " not in stem and "_" not in stem:
            exact.append((row["name"], row["path"]))
    return exact


def queries_that_moved(engine: SearchEngine, labelled_queries) -> list[str]:
    """Queries whose scores differ between learning off and learning on.

    The strong form of "learning does nothing without history". A signal that
    only happens not to *hurt* is not the same claim as one that cannot act.
    """
    moved: list[str] = []
    for labelled in labelled_queries:
        off = scores(engine.search(labelled.query, limit=10, usage=False,
                                   now=MEASUREMENT_NOW))
        on = scores(engine.search(labelled.query, limit=10, usage=True,
                                  now=MEASUREMENT_NOW))
        if off != on:
            moved.append(labelled.query)
    return moved


# -- the measurements ---------------------------------------------------------

def cold_start(engine: SearchEngine, queries) -> dict[str, object]:
    """A user with no history must get the baseline's answer exactly."""
    identical = True
    differences: list[str] = []
    for query in queries:
        plain = order(engine.search(query, limit=10, usage=False, now=MEASUREMENT_NOW))
        learned = order(engine.search(query, limit=10, usage=True, now=MEASUREMENT_NOW))
        if plain != learned:
            identical = False
            differences.append(query)
    return {"identical": identical, "queries": len(queries), "differences": differences}


def determinism(engine: SearchEngine, query: str, *, times: int = 3) -> dict[str, object]:
    """The same query and the same history, more than once."""
    orders = [
        order(engine.search(query, limit=10, usage=True, now=MEASUREMENT_NOW))
        for _ in range(times)
    ]
    return {"stable": all(order == orders[0] for order in orders), "order": orders[0]}


def forgetting(engine_factory, history, query: str, *, shift_days: float = 1200.0):
    """The same number of opens, once recent and once old.

    ``engine_factory`` builds a clean engine over the same corpus, so the two
    histories differ in nothing but their age.
    """
    young = engine_factory()
    record(young, history)
    aged = engine_factory()
    record(aged, [
        UsageEvent(
            document=event.document,
            query=event.query,
            age_days=event.age_days + shift_days,
        )
        for event in history
    ])
    plain = engine_factory()
    young_order = order(young.search(query, limit=10, usage=True, now=MEASUREMENT_NOW))
    old_order = order(aged.search(query, limit=10, usage=True, now=MEASUREMENT_NOW))
    baseline_order = order(plain.search(query, limit=10, usage=False,
                                         now=MEASUREMENT_NOW))
    return {
        "young": young_order,
        "old": old_order,
        "baseline": baseline_order,
        # A decayed history that has not returned to the baseline order has not
        # forgotten; that is the honest test.
        "old_is_baseline": old_order == baseline_order,
        "young_is_baseline": young_order == baseline_order,
    }


def max_share() -> dict[str, float]:
    """The largest share of the final score learning can take.

    The number is structural, not measured from a run: the weight is known and
    every signal is bounded to [0, 1], so the share is
    ``weight / (total + weight)``. The comparison that matters is against
    ``recency``, which ``docs/RANKING.md`` already documents as the bounded,
    secondary signal.
    """
    total = DEFAULT_WEIGHTS.total
    usage_share = ACTIVATED_USAGE_WEIGHT / (total + ACTIVATED_USAGE_WEIGHT)
    recency_share = DEFAULT_WEIGHTS.recency / total
    exact_share = DEFAULT_WEIGHTS.filename_exact / total
    phrase_share = DEFAULT_WEIGHTS.phrase_exact / total
    return {
        "usage": round(usage_share, 6),
        "recency": round(recency_share, 6),
        "filename_exact": round(exact_share, 6),
        "phrase_exact": round(phrase_share, 6),
        "total_weight": round(total, 3),
        "usage_weight": ACTIVATED_USAGE_WEIGHT,
    }


def headroom_ratio() -> float:
    """How many times learning could grow before it could overturn a recency.

    Below 1.0 means learning is already the weaker signal; the project would
    want it comfortably under, and the number is reported rather than argued.
    """
    shares = max_share()
    return round(shares["usage"] / shares["recency"], 4)


def weights_after_learning(activated: bool) -> RankingWeights:
    if activated:
        return RankingWeights(usage=ACTIVATED_USAGE_WEIGHT)
    return DEFAULT_WEIGHTS


def signal_report(events_by_document, *, query: str = "") -> dict[str, object]:
    """What the engine would compute from a history, without an engine.

    Useful on its own: it is the only way to see the decay and the scoping
    applied to a history you can print.
    """
    signals = learn.signals_by_document(events_by_document, query=query)
    report = learn.summary(signals)
    report["notes"] = {
        identifier: signal.note()
        for identifier, signal in sorted(signals.items())
        if signal.note()
    }
    return report


def events_to_index(events) -> dict[str, list]:
    """A history as ``{document_id: [(query, age_days)]}``."""
    grouped: dict[str, list] = {}
    for event in events:
        grouped.setdefault(event.document, []).append((event.query, event.age_days))
    return grouped


def index_corpus(root: Path, documents) -> SearchEngine:
    """Build an engine over the fixed evaluation corpus."""
    from universal_search.index.database import SearchDatabase
    from universal_search.index.indexer import Indexer

    database = SearchDatabase(Path(root) / "learn.db")
    Indexer(database).index_root(Path(root))
    return SearchEngine(database)


def corpus_with_documents(root: Path, documents):
    """Write the corpus to ``root`` and return it."""
    for document in documents:
        target = Path(root) / f"{document.id}{Path(document.path).suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.content, encoding="utf-8")
    return documents


def connection_counts(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]