import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.ranking import Candidate, Ranker, query_terms


RESULTS_SQL = """
    SELECT d.path, d.name, d.source, d.extension, d.modified_at,
           documents_fts.content AS content,
           snippet(documents_fts, 3, '[', ']', '…', 18) AS snippet,
           bm25(documents_fts) AS rank
    FROM documents_fts
    JOIN documents AS d ON d.id = documents_fts.document_id
    WHERE documents_fts MATCH ?
    ORDER BY rank, d.path
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

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        terms = query_terms(query)
        if not terms:
            return []
        pool = self.candidate_pool or max(limit * 5, 50)
        with self.database.connect() as connection:
            try:
                rows = connection.execute(RESULTS_SQL, (query, pool)).fetchall()
            except sqlite3.OperationalError:
                # The query used FTS5 operators incorrectly; retry as plain text.
                fallback = sanitize_query(query)
                if not fallback:
                    return []
                rows = connection.execute(RESULTS_SQL, (fallback, pool)).fetchall()

        ranked: list[tuple[float, Candidate, sqlite3.Row]] = []
        for row in rows:
            candidate = Candidate(
                name=row["name"],
                path=row["path"],
                content=row["content"],
                source=row["source"],
                modified_at=row["modified_at"],
                bm25_rank=row["rank"],
            )
            score = self.ranker.score(candidate, terms)
            ranked.append((score, candidate, row))
        ranked.sort(key=lambda item: (-item[0], item[1].path))

        results: list[SearchResult] = []
        for score, candidate, row in ranked[:limit]:
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
                )
            )
        return results
