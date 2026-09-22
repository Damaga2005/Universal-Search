from dataclasses import dataclass
from pathlib import Path

from universal_search.index.database import SearchDatabase


@dataclass(frozen=True, slots=True)
class SearchResult:
    path: Path
    name: str
    source: str
    snippet: str | None
    rank: float


class SearchEngine:
    def __init__(self, database: SearchDatabase) -> None:
        self.database = database

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        if not query.strip():
            return []
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT d.path, d.name, d.source,
                       snippet(documents_fts, 3, '[', ']', '…', 18) AS snippet,
                       bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents AS d ON d.id = documents_fts.document_id
                WHERE documents_fts MATCH ?
                ORDER BY rank LIMIT ?
            """, (query, limit)).fetchall()
        return [SearchResult(Path(r["path"]), r["name"], r["source"], r["snippet"], float(r["rank"])) for r in rows]
