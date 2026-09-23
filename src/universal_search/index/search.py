import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path

from universal_search.context import (
    Context,
    context_boost_for,
    expansion_terms,
    usage_boost_from,
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
    SELECT d.path, d.name, d.source, d.extension, d.modified_at,
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

USAGE_COUNTS_SQL = """
    SELECT document_id, COUNT(*) AS opens
    FROM usage_events
    WHERE document_id IN ({placeholders})
    GROUP BY document_id
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
    SELECT d.path, d.name, d.source, d.extension, d.modified_at,
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
    explain: dict[str, float] | None = None
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
    ) -> list[SearchResult]:
        """Rank ``query`` and record latency + result count (spec 011).

        Thin wrapper: every CLI/GUI/service path funnels through here, so
        the local metrics observe real usage — counters only, never the
        query text itself.
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
            usage_counts = (
                self._usage_counts(connection, rows) if usage and rows else {}
            )

        weights = self.ranker.weights
        if context is not None and weights.context == 0.0:
            weights = replace(weights, context=ACTIVATED_CONTEXT_WEIGHT)
        if usage_counts and weights.usage == 0.0:
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
                context_boost, notes = context_boost_for(
                    context,
                    path=row["path"],
                    source=row["source"],
                    doc_type=row["extension"],
                    modified_at=row["modified_at"],
                )
            usage_boost = usage_boost_from(usage_counts.get(row["document_id"], 0))
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
                    explain=points,
                    explain_notes=notes,
                )
            )
        return results

    # -- local usage learning (privacy: only recorded when enabled) ----------

    @staticmethod
    def _usage_counts(connection, rows) -> dict[str, int]:
        identifiers = list({row["document_id"] for row in rows})
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        fetched = connection.execute(
            USAGE_COUNTS_SQL.format(placeholders=placeholders), identifiers
        ).fetchall()
        return {row["document_id"]: row["opens"] for row in fetched}

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

    def clear_usage(self) -> int:
        """Delete every usage event; returns how many were removed."""
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM usage_events"
            ).fetchone()[0]
            connection.execute("DELETE FROM usage_events")
            connection.commit()
        return count
