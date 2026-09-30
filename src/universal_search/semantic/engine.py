"""Hybrid search: lexical first, semantic fallback only (phase 026).

The hybrid engine wraps the authoritative :class:`SearchEngine` and a
:class:`SemanticIndex`. The contract that makes it safe:

* The lexical engine always runs first and its results are returned
  unchanged. Exact filenames, exact phrases, filters and query operators
  are fully authoritative — the semantic layer can never reorder,
  replace or outrank a lexical result.
* The semantic layer is consulted **only** when the lexical engine returns
  nothing. It then contributes candidates ranked by n-gram cosine
  similarity, above a threshold that separates a meaningful overlap from
  an incidental one (so a "must retrieve nothing" query stays empty).

With no index configured, or with the derived tables removed, the hybrid
engine is exactly the lexical engine — the layer is optional and removable.
"""

from __future__ import annotations

from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult
from universal_search.semantic.index import SemanticIndex

_DOCUMENT_LOOKUP_SQL = """
    SELECT path, name, source, extension, modified_at, availability, id
    FROM documents WHERE id = ?
"""


class HybridSearchEngine:
    """Lexical search with an optional, fallback-only semantic layer."""

    def __init__(
        self,
        engine: SearchEngine,
        semantic: SemanticIndex | None = None,
    ) -> None:
        self.engine = engine
        self.semantic = semantic

    @property
    def database(self) -> SearchDatabase:
        return self.engine.database

    def search(
        self,
        query: str,
        limit: int = 20,
        **kwargs,
    ) -> list[SearchResult]:
        """Lexical results, or semantic candidates when lexical is empty.

        Every keyword argument (``context``, ``usage``, ``explain``,
        ``source``, ``doc_type``) is forwarded to the lexical engine
        unchanged; the semantic fallback ignores them because it only ever
        runs when there is nothing to filter, rank or explain.
        """
        results = self.engine.search(query, limit=limit, **kwargs)
        # Filters are user intent, not a ranking hint. The fallback has no
        # equivalent of the parsed source/type plan, so it must stay disabled
        # whenever either filter is present; otherwise a filtered query that
        # the lexical engine excluded could be resurrected semantically.
        filtered = (
            kwargs.get("source") is not None
            or kwargs.get("doc_type") is not None
        )
        if results or self.semantic is None or filtered:
            return results
        return self._semantic_fallback(query, limit)

    def _semantic_fallback(self, query: str, limit: int) -> list[SearchResult]:
        """Rank semantic candidates for a query the lexical engine missed."""
        self.semantic.ensure_fresh()
        candidates = self.semantic.search(query, limit=limit)
        if not candidates:
            return []
        identifiers = [doc_id for doc_id, _ in candidates]
        placeholders = ",".join("?" for _ in identifiers)
        with self.database.connect() as connection:
            rows = {
                str(row["id"]): row
                for row in connection.execute(
                    f"SELECT id, path, name, source, extension, modified_at,"
                    f" availability FROM documents WHERE id IN ({placeholders})",
                    identifiers,
                ).fetchall()
            }
        results: list[SearchResult] = []
        for doc_id, similarity in candidates:
            row = rows.get(doc_id)
            if row is None:
                continue
            results.append(
                SearchResult(
                    path=Path(row["path"]),
                    name=row["name"],
                    source=row["source"],
                    snippet=None,
                    rank=0.0,
                    score=similarity,
                    availability=row["availability"],
                    document_id=doc_id,
                    explain={"semantic_similarity": round(similarity, 4)},
                )
            )
        return results

    # -- passthroughs for the lexical engine's other responsibilities -------

    def record_open(self, document_id: str, query: str = "") -> None:
        self.engine.record_open(document_id, query)

    def usage_rows(self, limit: int = 50):
        return self.engine.usage_rows(limit)

    def clear_usage(self) -> int:
        return self.engine.clear_usage()
