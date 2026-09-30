"""Phase 031: bounded, versioned storage for the blocking fingerprints.

Derived data beside the canonical documents, exactly like the relationship
graph (phase 022) and the semantic index (phase 026):

* search never *requires* it — deleting every row makes search lexical again;
* it is rebuilt from canonical content and versioned, so incompatible
  fingerprints are rebuilt rather than silently reinterpreted;
* its size is bounded per document, not per byte, so a huge file cannot make
  the index explode.

Two tables, nothing more:

``document_fuzzy_terms``
    (ngram, document_id, weight, version, preprocessing_version). One row per
    kept trigram, primary key (ngram, document_id).

``document_fuzzy_metadata``
    (key, value): count, version, preprocessing_version, dirty.
"""

from __future__ import annotations

from contextlib import closing

from universal_search.index.database import SearchDatabase
from universal_search.fuzzy.verify import MAX_VERIFY_CHARS
from universal_search.fuzzy.trigrams import (
    MAX_TRIGRAMS_PER_DOC,
    MIN_TOKEN_CHARS,
    fold,
    selective_trigrams,
    tokens_of,
)


# Bump when the blocking or verification contract changes: stored rows carry
# the version, so incompatible fingerprints are rebuilt, not reinterpreted.
FUZZY_VERSION = 1
FUZZY_PREPROCESSING_VERSION = 1

_COUNT_KEY = "count"
_VERSION_KEY = "version"
_PREPROCESSING_KEY = "preprocessing_version"
_DIRTY_KEY = "dirty"

MAX_FUZZY_CHARS = 200_000


class FuzzyIndex:
    """Owns the derived fingerprints and produces verified candidates."""

    def __init__(
        self,
        database: SearchDatabase,
        *,
        limit: int = MAX_TRIGRAMS_PER_DOC,
    ) -> None:
        self.database = database
        self.limit = limit

    # -- maintenance ---------------------------------------------------------

    def rebuild(self) -> int:
        """Full rebuild from canonical content. Returns the document count.

        Two passes on purpose. The first counts how many documents contain each
        trigram; the second needs those counts to decide which trigrams are
        selective enough to be worth one of the 64 slots. Doing it in one pass
        would have to either store everything or use a guess.
        """
        documents = self._documents()
        document_frequency: dict[str, int] = {}
        for _document_id, name, content in documents:
            folded = self._embeddable(name, content)
            for word in set(tokens_of(folded)):
                if len(word) >= MIN_TOKEN_CHARS:
                    document_frequency[word] = document_frequency.get(word, 0) + 1

        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN")
            try:
                connection.execute("DELETE FROM document_fuzzy_terms")
                connection.execute("DELETE FROM document_fuzzy_documents")
                connection.execute("DELETE FROM document_fuzzy_metadata")
                term_rows: list[tuple[str, int, float, int, int]] = []
                for document_id, name, content in documents:
                    kept = selective_trigrams(
                        self._embeddable(name, content),
                        document_frequency,
                        limit=self.limit,
                    )
                    if not kept:
                        continue
                    surrogate = int(
                        connection.execute(
                            "INSERT INTO document_fuzzy_documents (document_id)"
                            " VALUES (?)",
                            (document_id,),
                        ).lastrowid
                    )
                    for ngram, weight in kept.items():
                        term_rows.append(
                            (
                                ngram,
                                surrogate,
                                weight,
                                FUZZY_VERSION,
                                FUZZY_PREPROCESSING_VERSION,
                            )
                        )
                connection.executemany(
                    "INSERT INTO document_fuzzy_terms"
                    " (ngram, surrogate, weight, version,"
                    " preprocessing_version) VALUES (?, ?, ?, ?, ?)",
                    term_rows,
                )
                for key, value in (
                    (_COUNT_KEY, str(len(documents))),
                    (_VERSION_KEY, str(FUZZY_VERSION)),
                    (_PREPROCESSING_KEY, str(FUZZY_PREPROCESSING_VERSION)),
                ):
                    connection.execute(
                        "INSERT INTO document_fuzzy_metadata (key, value)"
                        " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                        " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                        (key, value),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return len(documents)

    def _documents(self) -> list[tuple[str, str, str | None]]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT d.id, d.name, f.content FROM documents d"
                " JOIN documents_fts f ON f.document_id = d.id"
            ).fetchall()
        return [
            (str(row["id"]), str(row["name"]), row["content"])
            for row in rows
        ]

    @staticmethod
    def _embeddable(name: str, content: str | None) -> str:
        """Name plus a bounded slice of content, folded once.

        The bound is on the *fingerprint*, not on the content: verification
        reads the real text separately, and it is bounded by its own budget.
        """
        return fold(f"{name} {content or ''}"[:MAX_FUZZY_CHARS])

    def ensure_fresh(self, connection=None) -> None:
        if self.is_dirty(connection=connection):
            self.rebuild()

    def mark_dirty(self) -> None:
        with closing(self.database.connect()) as connection:
            connection.execute(
                "INSERT INTO document_fuzzy_metadata (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = CURRENT_TIMESTAMP",
                (_DIRTY_KEY, "1"),
            )
            connection.commit()

    def is_dirty(self, connection=None) -> bool:
        return self._metadata(_DIRTY_KEY, connection=connection) == "1"

    def _metadata(self, key: str, connection=None) -> str | None:
        owned = connection is None
        connection = connection or self.database.connect()
        try:
            row = connection.execute(
                "SELECT value FROM document_fuzzy_metadata WHERE key = ?", (key,)
            ).fetchone()
        finally:
            if owned:
                connection.close()
        return str(row["value"]) if row else None

    def count(self) -> int:
        value = self._metadata(_COUNT_KEY)
        return int(value) if value else 0

    def version(self) -> int:
        value = self._metadata(_VERSION_KEY)
        return int(value) if value else 0

    # -- removal -------------------------------------------------------------

    def remove_all(self) -> int:
        with closing(self.database.connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM document_fuzzy_documents"
            ).fetchone()[0]
            connection.execute("DELETE FROM document_fuzzy_terms")
            connection.execute("DELETE FROM document_fuzzy_documents")
            connection.execute("DELETE FROM document_fuzzy_metadata")
            connection.commit()
        return int(count)

    # -- blocking ------------------------------------------------------------

    def candidates(
        self, query_ngrams: set[str], *, cap: int = 50, minimum: float = 0.2,
        connection=None,
    ) -> list[tuple[str, float]]:
        """Documents that share enough trigrams with the query, best first.

        This is a *filter*. Nothing here is a match: the caller must verify
        every candidate against the real text.
        """
        return self.candidates_for(
            [query_ngrams], cap=cap, minimum=minimum, connection=connection
        )

    def candidates_for(
        self,
        token_ngrams: list[set[str]],
        *,
        cap: int = 50,
        minimum: float = 0.2,
        connection=None,
    ) -> list[tuple[str, float]]:
        """Candidates for a **per token** blocking, unioned.

        Blocking each token separately and taking the union is what makes
        multi-word queries work. A global overlap over the whole query is
        dominated by its long tokens and drowns a misspelled short one, which
        is how the first implementation lost "eberts moll".

        All tokens are fetched with a **single** query. The phase-031 latency
        gate measured one connection per operation as the dominant cost of
        this layer, because every connection re-runs the connection PRAGMAs.
        """
        if not token_ngrams:
            return []
        union = set().union(*token_ngrams)
        if not union:
            return []
        shared_by_token = self._postings_shared(
            token_ngrams, minimum, connection=connection
        )
        best: dict[str, float] = {}
        for shared in shared_by_token:
            for document_id, score in shared.items():
                if score > best.get(document_id, 0.0):
                    best[document_id] = score
        ordered = sorted(best.items(), key=lambda row: (-row[1], row[0]))
        return ordered[:cap]

    def _postings_shared(
        self,
        token_ngrams: list[set[str]],
        minimum: float,
        *,
        connection=None,
    ) -> list[dict[str, float]]:
        """Per-token overlap, from one query over the union of all trigrams."""
        union = set().union(*token_ngrams)
        placeholders = ",".join("?" for _ in union)
        owned = connection is None
        connection = connection or self.database.connect()
        try:
            rows = connection.execute(
                "SELECT t.ngram, d.document_id FROM document_fuzzy_terms t"
                " JOIN document_fuzzy_documents d ON d.surrogate = t.surrogate"
                f" WHERE t.ngram IN ({placeholders})"
                f" AND t.version = {FUZZY_VERSION}"
                f" AND t.preprocessing_version = {FUZZY_PREPROCESSING_VERSION}",
                list(union),
            ).fetchall()
        finally:
            if owned:
                connection.close()
        per_token: list[dict[str, set[str]]] = [{} for _ in token_ngrams]
        for row in rows:
            ngram = str(row["ngram"])
            document_id = str(row["document_id"])
            for index, ngrams in enumerate(token_ngrams):
                if ngram in ngrams:
                    per_token[index].setdefault(document_id, set()).add(ngram)
        return [
            {
                document_id: len(found) / len(token_ngrams[index])
                for document_id, found in shared.items()
                if len(found) / len(token_ngrams[index]) >= minimum
            }
            for index, shared in enumerate(per_token)
        ]

    def document_rows(
        self, document_ids: list[str], *, max_chars: int = MAX_VERIFY_CHARS,
        connection=None,
    ) -> dict[str, dict[str, str]]:
        """Everything verification and the result row need, in **one** query.

        Two decisions, both measured by the phase-031 latency gate:

        * **One round trip, and one connection for the whole fallback.** The
          first version opened a connection per document, and every connection
          re-runs the connection PRAGMAs, which dominated the layer's cost.
        * **The text slice is bounded in SQL**, not in Python. Fetching whole
          contents for up to fifty candidates meant pulling megabytes per
          query, and verification never reads past its own budget anyway.
        """
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        owned = connection is None
        connection = connection or self.database.connect()
        try:
            rows = connection.execute(
                "SELECT d.id, d.name, d.path, d.source, d.availability,"
                " substr(f.content, 1, ?) AS content"
                " FROM documents d"
                " JOIN documents_fts f ON f.document_id = d.id"
                f" WHERE d.id IN ({placeholders})",
                (max_chars, *document_ids),
            ).fetchall()
        finally:
            if owned:
                connection.close()
        return {
            str(row["id"]): {
                "name": str(row["name"]),
                "text": str(row["content"] or ""),
                "path": str(row["path"]),
                "source": str(row["source"] or "local"),
                "availability": str(row["availability"] or "available"),
            }
            for row in rows
        }

    def document_texts(
        self, document_ids: list[str], *, max_chars: int = MAX_VERIFY_CHARS,
        connection=None,
    ) -> dict[str, tuple[str, str]]:
        """Name and bounded text for each candidate, in one query."""
        return {
            document_id: (row["name"], row["text"])
            for document_id, row in self.document_rows(
                document_ids, max_chars=max_chars, connection=connection
            ).items()
        }

    def document_text(self, document_id: str) -> tuple[str, str]:
        """The real name and text of one document, for verification."""
        return self.document_texts([document_id]).get(document_id, ("", ""))

    def row_count(self) -> int:
        """Rows in the derived table.

        The size gate of phase 031 is stated in rows per document, which is
        the only number that is independent of SQLite's page layout. Real
        on-disk growth is measured by comparing the database file before and
        after a rebuild, which is what the evidence gate does.
        """
        with closing(self.database.connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM document_fuzzy_terms"
                ).fetchone()[0]
            )
