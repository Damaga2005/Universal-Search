import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from universal_search.index.database import SearchDatabase


RESULTS_SQL = """
    SELECT d.path, d.name, d.source,
           snippet(documents_fts, 3, '[', ']', '…', 18) AS snippet,
           bm25(documents_fts) AS rank
    FROM documents_fts
    JOIN documents AS d ON d.id = documents_fts.document_id
    WHERE documents_fts MATCH ?
    ORDER BY rank LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class SearchResult:
    path: Path
    name: str
    source: str
    snippet: str | None
    rank: float


def sanitize_query(query: str) -> str:
    """Translate free text into a safe FTS5 query of quoted terms.

    Returns an empty string when the query contains no searchable terms.
    """
    terms = re.findall(r"\w+", query)
    if not terms:
        return ""
    return " AND ".join(f'"{term}"' for term in terms)


class SearchEngine:
    def __init__(self, database: SearchDatabase) -> None:
        self.database = database

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        if not query.strip():
            return []
        with self.database.connect() as connection:
            try:
                rows = connection.execute(RESULTS_SQL, (query, limit)).fetchall()
            except sqlite3.OperationalError:
                # The query used FTS5 operators incorrectly; retry as plain text.
                fallback = sanitize_query(query)
                if not fallback:
                    return []
                rows = connection.execute(RESULTS_SQL, (fallback, limit)).fetchall()
        return [
            SearchResult(Path(r["path"]), r["name"], r["source"], r["snippet"], float(r["rank"]))
            for r in rows
        ]
