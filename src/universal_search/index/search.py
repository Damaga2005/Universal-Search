import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from universal_search.context import (
    Context,
    context_boost_for,
    expansion_terms,
)
from universal_search.index.database import SearchDatabase
from universal_search.metrics import record_search
from universal_search.index.ranking import (
    ACTIVATED_CONTEXT_WEIGHT,
    ACTIVATED_USAGE_WEIGHT,
    Candidate,
    Ranker,
)
from universal_search.query import parse_query, translate


# Candidate pool first (FTS match + bm25 order + LIMIT), THEN join the
# metadata table.  Joining `documents` for every matched row before the
# LIMIT cost ~6ms on a 2000-doc index; evaluated over the pool only the
# same query runs in ~10.5ms instead of ~16.7ms (profiled, 2026-09).
#
# The snippet is NOT built by FTS5 `snippet()`: that function walks every
# phrase instance, so one document that repeats a term thousands of times
# took 1.9 s (and >150 s at 2 MB) while MATCH and bm25 stayed instant
# (measured, phase 018). `build_snippet()` below is a single linear pass,
# so latency no longer depends on how often a term occurs.
RESULTS_SQL = """
    SELECT d.path, d.name, d.source, d.extension, d.modified_at, d.size,
           d.availability, d.id AS document_id,
           f.content AS content,
           f.rank AS rank
    FROM (
        SELECT document_id, content,
               bm25(documents_fts) AS rank
        FROM documents_fts
        WHERE documents_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    ) AS f
    JOIN documents AS d ON d.id = f.document_id
    ORDER BY f.rank, d.path
"""

# Phase 044. Before this it counted opens and ignored which query they happened
# under, so the `query` column had been recorded since phase 008 and read by
# exactly one query -- the one a user inspects. Learning that cannot tell one
# task from another is noise with extra storage. `opened_at` comes back for the
# same reason: an event from 2024 used to carry full weight.
USAGE_EVENTS_SQL = """
    SELECT document_id, query, opened_at
    FROM usage_events
    WHERE document_id IN ({placeholders})
"""

USAGE_ROWS_SQL = """
    SELECT u.opened_at, u.query, u.document_id, u.id,
           COALESCE(d.name, u.document_id) AS name, d.path
    FROM usage_events AS u
    LEFT JOIN documents AS d ON d.id = u.document_id
    ORDER BY u.id DESC
    LIMIT ?
"""

SNIPPET_RADIUS = 80


def _age_days(opened_at: str, now: datetime) -> float:
    """How old a usage event is, in days, against an injected clock.

    An unparsable or absent timestamp is treated as very old rather than as
    new: an event whose age cannot be established is not evidence of a recent
    habit, and guessing "new" would give it the strongest possible weight.
    """
    from universal_search.learn import UNBOUNDED_AGE_DAYS

    if not opened_at:
        return float(UNBOUNDED_AGE_DAYS)
    text = str(opened_at).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return float(UNBOUNDED_AGE_DAYS)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds() / 86400.0)


def _pool_sql(clauses: list[str]) -> str:
    """Results SQL with optional filters joined INSIDE the pool subquery.

    Filters sit between the match and the LIMIT, so a filtered search ranks
    the whole filtered set instead of whatever the unfiltered pool happened
    to pick first (spec 009). Placeholder order stays match, filters,
    limit. Clause templates are static strings built by the query
    translator or by the ``source``/``doc_type`` keyword arguments — never
    user text; every value is bound by the caller.
    """
    if not clauses:
        return RESULTS_SQL
    return RESULTS_SQL.replace(
        "FROM documents_fts\n        WHERE documents_fts MATCH ?",
        "FROM documents_fts\n"
        "        JOIN documents AS d ON d.id = documents_fts.document_id\n"
        "        WHERE documents_fts MATCH ? AND "
        + " AND ".join(clauses),
        1,
    )


# Filter-only queries (`type:pdf`, `size:>10MB`): there is no text to rank,
# so rows are read straight from SQL — newest first, score 0.0 (spec 012).
# `{where}` only ever receives the static clause templates above; every
# value stays a bound parameter.
FILTER_ONLY_SQL = """
    SELECT d.path, d.name, d.source, d.extension, d.modified_at, d.size,
           d.availability, d.id AS document_id,
           NULL AS content, NULL AS snippet, 0.0 AS rank
    FROM documents AS d
    WHERE {where}
    ORDER BY d.modified_at DESC, d.path
    LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class SearchResult:
    path: Path
    name: str
    source: str
    snippet: str | None
    rank: float
    score: float = 0.0
    availability: str = "available"
    document_id: str = ""
    # Phase 036: presentation metadata. Sorting and grouping by date or size
    # need them, and carrying them here means the GUI can show them without a
    # second query per row.
    modified_at: str | None = None
    size: int = 0
    # The annotation said `dict[str, float]` and was wrong: the phase 031 fuzzy
    # layer puts structured match details in here alongside its numeric
    # `fuzzy_overlap`, and `tests/test_fuzzy_search.py` pins that shape. Phase
    # 042 found it by putting `explain` on screen, where a list where a number
    # was declared does not survive contact with `"{:.3f}"`. The value is
    # therefore `object`: a consumer that formats it has to say what it does
    # with a number and with something that is not one.
    explain: dict[str, object] | None = None
    explain_notes: tuple[str, ...] = ()


def filters_only_results(
    database: SearchDatabase,
    clauses: list[str],
    params: list[object],
    limit: int,
) -> list[SearchResult]:
    """Resolve a query made only of SQL filters, with no text part (012).

    There is nothing to match and nothing to rank, so the recency order of
    the filtered rows is the result: every ``SearchResult`` carries score
    0.0 and no snippet.
    """
    if not clauses:
        return []
    sql = FILTER_ONLY_SQL.replace("{where}", " AND ".join(clauses), 1)
    with database.connect() as connection:
        rows = connection.execute(sql, [*params, limit]).fetchall()
    return [
        SearchResult(
            path=Path(row["path"]),
            name=row["name"],
            source=row["source"],
            snippet=None,
            rank=0.0,
            score=0.0,
            availability=row["availability"],
            document_id=row["document_id"],
            modified_at=row["modified_at"],
            size=int(row["size"] or 0),
        )
        for row in rows
    ]


def build_snippet(content: str | None, terms: tuple[str, ...]) -> str | None:
    """Short excerpt of ``content`` centred on the first matching term.

    Replaces FTS5's ``snippet()`` (phase 018): that function walks every
    phrase instance, so its cost grows with how often a term occurs — a
    log with 10 000 mentions of one word took 1.9 s. This is a single
    linear pass with a bounded window, so a pathological document costs
    the same order as a normal one.
    """
    if not content or not terms:
        return None
    lowered = content.casefold()
    positions = [index for term in terms if (index := lowered.find(term)) >= 0]
    if not positions:
        return None
    start = max(0, min(positions) - SNIPPET_RADIUS)
    end = min(len(content), min(positions) + SNIPPET_RADIUS)
    excerpt = content[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{excerpt}{suffix}"


class SearchEngine:
    def __init__(
        self,
        database: SearchDatabase,
        ranker: Ranker | None = None,
        candidate_pool: int | None = None,
    ) -> None:
        self.database = database
        self.ranker = ranker or Ranker()
        self.candidate_pool = candidate_pool

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        context: Context | None = None,
        usage: bool = False,
        explain: bool = False,
        source: str | None = None,
        doc_type: str | None = None,
        now: datetime | None = None,
    ) -> list[SearchResult]:
        """Rank ``query`` and record latency + result count (spec 011).

        Thin wrapper: every CLI/GUI/service path funnels through here, so
        the local metrics observe real usage — counters only, never the
        query text itself. ``now`` passes the clock through to the ranking
        layer; see :meth:`_search`.
        """
        started = time.perf_counter()
        results = self._search(
            query,
            limit,
            context=context,
            usage=usage,
            explain=explain,
            source=source,
            doc_type=doc_type,
            now=now,
        )
        record_search((time.perf_counter() - started) * 1000, len(results))
        return results

    def _search(
        self,
        query: str,
        limit: int = 20,
        *,
        context: Context | None = None,
        usage: bool = False,
        explain: bool = False,
        source: str | None = None,
        doc_type: str | None = None,
        now: datetime | None = None,
    ) -> list[SearchResult]:
        """Rank the index for ``query``.

        ``context`` applies the personal-context layer: related terms widen
        retrieval only (scoring keeps the original terms) and a small bounded
        boost marks documents preferred by that context. ``usage`` adds the
        local usage signal when the user enabled learning (privacy:
        disabled by default). ``explain`` returns the full scoring
        breakdown so any personalization can be inspected. ``source`` and
        ``doc_type`` are SQL-level filters — still pure index queries, the
        filesystem is never touched during a search.

        ``now`` is the clock the usage decay and the recency signal are
        measured against. Phase 044 made both accept one, because a ranking
        signal that reads the clock itself cannot be tested for determinism.

        ``query`` goes through the query language of spec 012 (lexer, parser,
        validation, translation). A malformed query raises
        :class:`~universal_search.query.QueryError` before any I/O, so it
        never reaches the database nor the metrics recorder. Query
        language filters and the ``source``/``doc_type`` keyword arguments
        are combined; a query made only of filters is resolved from SQL in
        recency order with score 0.0, and a query whose text part is empty
        or negative-only returns no results, because FTS5 has no unary NOT
        to anchor exclusions on.
        """
        plan = translate(parse_query(query))
        terms = plan.terms

        clauses: list[str] = list(plan.sql)
        params: list[object] = list(plan.sql_params)
        if source:
            clauses.append("d.source = ?")
            params.append(source)
        if doc_type:
            clauses.append("d.extension = ?")
            params.append(
                doc_type if str(doc_type).startswith(".") else f".{doc_type}"
            )

        if not terms:
            if plan.sql and not plan.negations:
                return filters_only_results(
                    self.database, clauses, params, limit
                )
            # Empty, or exclusions with no positive term to anchor them to:
            # an empty query means "no filter", not "everything".
            return []
        pool = self.candidate_pool or max(limit * 5, 50)
        now = now or datetime.now(timezone.utc)

        expansions = expansion_terms(terms, context)
        base = plan.fts
        if expansions:
            # Recall widening only: the extra terms retrieve more candidates
            # but never enter scoring (exact matches keep all their signals).
            quoted = " OR ".join(f'"{term}"' for term in expansions)
            base = f"({base}) OR ({quoted})"
        match = f"({base}) NOT {plan.negations}" if plan.negations else base

        sql = _pool_sql(clauses)
        with self.database.connect() as connection:
            try:
                rows = connection.execute(
                    sql, [match, *params, pool]
                ).fetchall()
            except sqlite3.OperationalError:
                # Translation only emits quoted word runs, whitelisted columns
                # and known operators, so this retry should be unreachable;
                # it exists to survive a tokenizer disagreement without
                # dropping the user's exclusions.
                fallback = " AND ".join(f'"{term}"' for term in terms)
                if plan.negations:
                    fallback = f"({fallback}) NOT {plan.negations}"
                rows = connection.execute(
                    sql, [fallback, *params, pool]
                ).fetchall()
            usage_signals = (
                self._usage_signals(connection, rows, query=query, now=now)
                if usage and rows
                else {}
            )
            # Only documents whose learning actually does something raise the
            # weight. Phase 044: with the weight raised but no effective signal,
            # the denominator grew and every score moved for no reason, which is
            # the difference between "learning is on" and "learning changed
            # something".
            active_usage = {
                identifier: signal
                for identifier, signal in usage_signals.items()
                if not signal.is_inert
            }

        weights = self.ranker.weights
        if context is not None and weights.context == 0.0:
            weights = replace(weights, context=ACTIVATED_CONTEXT_WEIGHT)
        if active_usage and weights.usage == 0.0:
            weights = replace(weights, usage=ACTIVATED_USAGE_WEIGHT)
        ranker = Ranker(weights)

        ranked: list[
            tuple[float, Candidate, sqlite3.Row, dict[str, float] | None, tuple[str, ...]]
        ] = []
        for row in rows:
            candidate = Candidate(
                name=row["name"],
                path=row["path"],
                content=row["content"],
                source=row["source"],
                modified_at=row["modified_at"],
                bm25_rank=row["rank"],
            )
            context_boost = 0.0
            notes: tuple[str, ...] = ()
            if context is not None:
                context_boost, context_notes = context_boost_for(
                    context,
                    path=row["path"],
                    source=row["source"],
                    doc_type=row["extension"],
                    modified_at=row["modified_at"],
                )
                notes = context_notes
            usage_signal = active_usage.get(row["document_id"])
            usage_boost = usage_signal.boost if usage_signal else 0.0
            if usage_signal is not None and explain:
                # Phase 044's explainability rule. Usage used to move results
                # with nothing in the explanation saying why; the context signal
                # has had a sentence for it since phase 024.
                note = usage_signal.note()
                if note:
                    notes = notes + (note,)
            if explain:
                points, score = ranker.contributions(
                    candidate,
                    terms,
                    usage_boost=usage_boost,
                    context_boost=context_boost,
                )
            else:
                points = None
                score = ranker.score(
                    candidate,
                    terms,
                    usage_boost=usage_boost,
                    context_boost=context_boost,
                )
            ranked.append((score, candidate, row, points, notes))
        ranked.sort(key=lambda item: (-item[0], item[1].path))

        results: list[SearchResult] = []
        for score, candidate, row, points, notes in ranked[:limit]:
            # One linear pass over the content, bounded window: latency
            # does not depend on how many times a term occurs (phase 018).
            snippet = build_snippet(candidate.content, terms)
            results.append(
                SearchResult(
                    path=Path(candidate.path),
                    name=candidate.name,
                    source=candidate.source,
                    snippet=snippet,
                    rank=candidate.bm25_rank,
                    score=score,
                    availability=row["availability"],
                    document_id=row["document_id"],
                    modified_at=row["modified_at"],
                    size=int(row["size"] or 0),
                    explain=points,
                    explain_notes=notes,
                )
            )
        return results

    # -- local usage learning (privacy: only recorded when enabled) ----------

    @staticmethod
    def _usage_signals(connection, rows, *, query: str, now: datetime) -> dict:
        """What the learned events are worth for these candidates, today.

        ``now`` is passed in rather than read from the clock, for two reasons:
        the ranker already takes one for the recency signal, and a signal whose
        value changes between two calls in the same statement cannot be tested
        for determinism at all.
        """
        from universal_search import learn

        identifiers = sorted({row["document_id"] for row in rows})
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        fetched = connection.execute(
            USAGE_EVENTS_SQL.format(placeholders=placeholders), identifiers
        ).fetchall()
        grouped: dict[str, list] = {}
        for event in fetched:
            grouped.setdefault(event["document_id"], []).append(
                (event["query"], _age_days(event["opened_at"], now))
            )
        return learn.signals_by_document(grouped, query=query)

    def record_open(self, document_id: str, query: str = "") -> None:
        """Persist one local "result opened" signal (query/result association).

        Stored only in the local database; nothing is ever uploaded.
        """
        if not document_id:
            return
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO usage_events (document_id, query) VALUES (?, ?)",
                (document_id, query[:200]),
            )
            connection.commit()

    def usage_rows(self, limit: int = 50) -> list[sqlite3.Row]:
        """Inspectable usage log (what was opened, for which query)."""
        with self.database.connect() as connection:
            return connection.execute(USAGE_ROWS_SQL, (limit,)).fetchall()

    def usage_effects(self, limit: int = 20, *, now: datetime | None = None) -> list[dict]:
        """What each learned (document, query) pair is worth *right now*.

        Phase 044's inspectable face. The raw log answers "what did I open";
        this answers the question the new model actually raises, which is "what
        is my history still doing to my results" -- which pairs survive the
        decay, and which have faded to nothing. An inspectable signal that can
        only be inspected as a list of raw rows is half inspectable.
        """
        from universal_search import learn

        now = now or datetime.now(timezone.utc)
        with self.database.connect() as connection:
            rows = connection.execute(USAGE_ROWS_SQL, (limit * 4,)).fetchall()
        grouped: dict[tuple[str, str], list] = {}
        meta: dict[tuple[str, str], tuple[str, str]] = {}
        for row in rows:
            query = row["query"] or ""
            key = (row["document_id"], query)
            grouped.setdefault(key, []).append(
                (query, _age_days(row["opened_at"], now))
            )
            meta.setdefault(key, (row["name"] or row["document_id"], row["path"] or ""))
        effects: list[dict] = []
        for (document_id, query), events in grouped.items():
            signal = learn.signal_for(document_id, events, query=query)
            name, path = meta[(document_id, query)]
            effects.append({
                "document_id": document_id,
                "name": name,
                "path": path,
                "query": query,
                "query_events": round(signal.query_events, 3),
                "global_events": round(signal.global_events, 3),
                "boost": round(signal.boost, 3),
                "note": signal.note(),
            })
        effects.sort(key=lambda item: (-item["boost"], item["query"], item["name"]))
        return effects[:limit]

    def clear_usage(self) -> int:
        """Delete every usage event; returns how many were removed."""
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM usage_events"
            ).fetchone()[0]
            connection.execute("DELETE FROM usage_events")
            connection.commit()
        return count
