import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from universal_search.context import (
    Context,
    context_boost_for,
    expansion_terms,
    usage_boost_from,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.ranking import (
    ACTIVATED_CONTEXT_WEIGHT,
    ACTIVATED_USAGE_WEIGHT,
    Candidate,
    Ranker,
    query_terms,
)


RESULTS_SQL = """
    SELECT d.path, d.name, d.source, d.extension, d.modified_at,
           d.availability, d.id AS document_id,
           documents_fts.content AS content,
           snippet(documents_fts, 3, '[', ']', '…', 18) AS snippet,
           bm25(documents_fts) AS rank
    FROM documents_fts
    JOIN documents AS d ON d.id = documents_fts.document_id
    WHERE documents_fts MATCH ?
    ORDER BY rank, d.path
    LIMIT ?
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


def sanitize_query(query: str) -> str:
    """Translate free text into a safe FTS5 query of quoted terms.

    Returns an empty string when the query contains no searchable terms.
    """
    terms = re.findall(r"\w+", query)
    if not terms:
        return ""
    return " AND ".join(f'"{term}"' for term in terms)


def build_snippet(content: str | None, terms: tuple[str, ...]) -> str | None:
    """Short excerpt of ``content`` centred on the first matching term.

    Used when the FTS snippet carries no highlight (name/path-only matches),
    which previously leaked the whole content into the result list.
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
    ) -> list[SearchResult]:
        """Rank the index for ``query``.

        ``context`` applies the personal-context layer: related terms widen
        retrieval only (scoring keeps the original terms) and a small bounded
        boost marks documents preferred by that context. ``usage`` adds the
        local usage signal when the user enabled learning (privacy:
        disabled by default). ``explain`` returns the full scoring
        breakdown so any personalization can be inspected.
        """
        terms = query_terms(query)
        if not terms:
            return []
        pool = self.candidate_pool or max(limit * 5, 50)

        expansions = expansion_terms(terms, context)
        match = query
        if expansions:
            # Recall widening only: the extra terms retrieve more candidates
            # but never enter scoring (exact matches keep all their signals).
            quoted = " OR ".join(f'"{term}"' for term in expansions)
            match = f"({sanitize_query(query)}) OR ({quoted})"

        with self.database.connect() as connection:
            try:
                rows = connection.execute(RESULTS_SQL, (match, pool)).fetchall()
            except sqlite3.OperationalError:
                # The query used FTS5 operators incorrectly; retry as plain text.
                fallback = sanitize_query(query)
                if not fallback:
                    return []
                rows = connection.execute(RESULTS_SQL, (fallback, pool)).fetchall()
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
            fts_snippet = row["snippet"]
            if fts_snippet and "[" in fts_snippet and "]" in fts_snippet:
                snippet = fts_snippet
            else:
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
