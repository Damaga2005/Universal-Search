"""Phase 031: fallback-only robust search for typos and partial words.

The contract that makes this safe to enable by default:

* The authoritative :class:`SearchEngine` always runs first and its results
  are returned unchanged. Exact filenames, exact phrases, filters and query
  operators stay authoritative; this layer can never reorder, replace or
  outrank a lexical result.
* The layer is consulted **only** when the lexical engine returns nothing, and
  every candidate it proposes is verified against the document's real text
  before it is returned. The trigram index proposes; it never decides.
* Filters disable it entirely: with ``source`` or ``doc_type`` present the
  layer is skipped, because it cannot reproduce the parsed filter plan. (This
  is the same rule the semantic layer of phase 026 learned the hard way.)

With no fingerprint index configured, or with the derived tables deleted, this
is exactly the lexical engine.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from universal_search.fuzzy.index import FuzzyIndex
from universal_search.fuzzy.trigrams import (
    MAX_CANDIDATES,
    MIN_TOKEN_CHARS,
    MIN_TOKEN_OVERLAP,
    fold,
    query_trigrams,
    tokens_of,
)
from universal_search.fuzzy.verify import (
    MAX_VERIFY_CHARS,
    is_nonsense,
    resolve_tokens,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.search import SearchEngine, SearchResult


class FuzzySearchEngine:
    """Lexical search with an optional, verified, fallback-only fuzzy layer."""

    def __init__(
        self,
        engine: SearchEngine,
        fuzzy: FuzzyIndex | None = None,
        *,
        candidates: int = MAX_CANDIDATES,
        minimum_overlap: float = MIN_TOKEN_OVERLAP,
        max_verify_chars: int = MAX_VERIFY_CHARS,
    ) -> None:
        self.engine = engine
        self.fuzzy = fuzzy
        self.candidates = candidates
        self.minimum_overlap = minimum_overlap
        self.max_verify_chars = max_verify_chars

    @property
    def database(self) -> SearchDatabase:
        return self.engine.database

    # Phase 042 added the three delegations below. This wrapper is only
    # transparent if it answers everything the engine it wraps answers, and
    # until now it did not: `HybridSearchEngine` forwards the usage signals
    # and this one silently did not, so stacking the two -- which is what the
    # CLI does and what the window now does -- meant anything reaching through
    # for `usage_rows` got an AttributeError. Found by phase 042, whose own
    # call site was the one that broke.

    def record_open(self, document_id: str, query: str = "") -> None:
        return self.engine.record_open(document_id, query)

    def usage_rows(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.engine.usage_rows(limit)

    def clear_usage(self) -> int:
        return self.engine.clear_usage()

    def search(self, query: str, limit: int = 10, **kwargs: Any) -> list[SearchResult]:
        results = self.engine.search(query, limit=limit, **kwargs)
        # Filters are user intent, not a ranking hint. This layer has no
        # equivalent of the parsed plan, so a filtered query must not fall
        # through to it: otherwise a filter that excluded everything would be
        # silently undone.
        filtered = (
            kwargs.get("source") is not None
            or kwargs.get("doc_type") is not None
        )
        if results or self.fuzzy is None or filtered:
            return results
        return self._fallback(query, limit, **kwargs)

    # -- internals -----------------------------------------------------------

    def _fallback(
        self, query: str, limit: int, **kwargs: Any
    ) -> list[SearchResult]:
        index = self.fuzzy
        assert index is not None
        # A query whose every token is nonsense is answered before any work:
        # this is the "must retrieve nothing" contract paying for itself.
        tokens = [
            token for token in tokens_of(query)
            if len(token) >= MIN_TOKEN_CHARS
        ]
        if not tokens or all(is_nonsense(token) for token in tokens):
            return []
        # One connection for the whole fallback. Every SQLite connection in
        # this project re-runs its PRAGMAs, and the phase-031 latency gate
        # measured one connection per operation as the layer's dominant cost:
        # 29 ms of the 39 ms, against 2.4 ms of actual verification work.
        with closing(self.database.connect()) as connection:
            index.ensure_fresh(connection=connection)
            # Block per token and union: a global overlap over the whole query
            # is dominated by its long tokens and drowns a misspelled short
            # one.
            proposed = index.candidates_for(
                [query_trigrams(token) for token in tokens],
                cap=self.candidates,
                minimum=self.minimum_overlap,
                connection=connection,
            )
            if not proposed:
                return []
            rows = index.document_rows(
                [document_id for document_id, _overlap in proposed],
                max_chars=self.max_verify_chars,
                connection=connection,
            )
        verified: list[tuple[float, SearchResult]] = []
        for document_id, overlap in proposed:
            row = rows.get(document_id)
            if row is None:
                continue
            # Fold each candidate once, not once per token.
            resolved, evidence = resolve_tokens(
                tokens,
                fold(row["name"]),
                fold(row["text"]),
                max_chars=self.max_verify_chars,
            )
            if not resolved:
                # The filter liked it; the truth did not. Drop it.
                continue
            result = self._result_for(document_id, row, overlap, evidence)
            if result is not None:
                verified.append((overlap, result))
        verified.sort(key=lambda row: (-row[0], row[1].path.name))
        return [result for _overlap, result in verified[:limit]]

    def _result_for(
        self,
        document_id: str,
        row: dict[str, str],
        overlap: float,
        evidence: tuple[tuple[str, str, int], ...],
    ) -> SearchResult | None:
        explain = {
            "fuzzy_overlap": round(overlap, 4),
            "fuzzy_matches": [
                {"token": token, "rule": rule, "distance": distance}
                for token, rule, distance in evidence
            ],
        }
        notes = tuple(
            f"typo/prefijo: {token} ({rule})"
            + (f" a distancia {distance}" if rule == "edit" else "")
            for token, rule, distance in evidence
            if rule in {"substring", "edit"}
        )
        return SearchResult(
            path=Path(row["path"]),
            name=row["name"],
            source=row["source"],
            snippet=_snippet(row["text"]),
            rank=0,
            score=round(overlap, 6),
            availability=row["availability"],
            document_id=document_id,
            explain=explain,
            explain_notes=notes,
        )


def _snippet(text: str, limit: int = 200) -> str:
    """A short context line, bounded. Never the whole document."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + " ..."


def engine_for(
    database: SearchDatabase,
    *,
    enabled: bool = True,
) -> SearchEngine | FuzzySearchEngine:
    """The search engine the CLI and the GUI should use.

    One place decides whether the extra layers exist, so ``--no-fuzzy`` and
    ``--no-semantic`` cannot drift apart between the two front ends.
    """
    lexical = SearchEngine(database)
    if not enabled:
        return lexical
    return FuzzySearchEngine(lexical, FuzzyIndex(database))
