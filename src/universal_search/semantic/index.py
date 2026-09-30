"""Versioned, rebuildable, removable semantic vector storage (phase 026).

The index is derived data beside the canonical documents, exactly like the
relationship graph: search never *requires* it, it is rebuilt from canonical
content, and deleting it costs nothing. It stores, per document, the
TF-IDF-weighted char n-gram vector (as an inverted index) plus the vector
norm, and it is versioned so incompatible derived data is rebuilt rather
than silently reinterpreted.

Rebuild strategy: the indexer marks the index dirty after a pass that
touched documents; the index rebuilds lazily on the next fallback search.
A full rebuild reads every document's content from the database, computes
corpus idf, and writes the vectors in one transaction. There is no
incremental idf — idf is corpus-level, so partial updates would be wrong.

The similarity threshold separates a meaningful n-gram overlap from an
incidental one. Measured (phase 026): incidental overlap (e.g. the "ist"/
"ste" n-grams shared by "existe" and "sistema") tops out near 0.09, while
every real failure query's relevant documents sit at >= 0.19.
"""

from __future__ import annotations

import json
import math
from contextlib import closing

from universal_search.index.database import SearchDatabase
from universal_search.semantic.ngram import (
    NGRAM_PREPROCESSING_VERSION,
    NGRAM_VERSION,
    SemanticProvider,
    content_words,
)

# Minimum cosine similarity for a semantic candidate to be returned. Below
# this the overlap is incidental (see module docstring).
MIN_SIMILARITY = 0.12

# A query word and a document word also count as shared when one is a prefix
# of the other and both are at least this long ("receta"/"recetas",
# "informe"/"informes"). The layer exists to catch morphological variants, and
# an exact-token gate silently blocked exactly that case. The length floor is
# what keeps the gate honest: a 3-4 character fragment ("nad" of "nada") is
# exactly the incidental overlap the gate exists to reject.
MIN_STEM_CHARS = 5

_DIRTY_KEY = "dirty"
_COUNT_KEY = "count"
_N_KEY = "n"
_VERSION_KEY = "version"
_PREPROCESSING_KEY = "preprocessing_version"


def _shares_word(query_words: set[str], doc_words: set[str]) -> bool:
    """True when a query word and a document word are the same variant.

    Exact equality, or a prefix relation between two words of at least
    ``MIN_STEM_CHARS`` characters. Longer words only: a short fragment is not
    evidence of anything.
    """
    if query_words & doc_words:
        return True
    for word in query_words:
        if len(word) < MIN_STEM_CHARS:
            continue
        for other in doc_words:
            if len(other) >= MIN_STEM_CHARS and (
                word.startswith(other) or other.startswith(word)
            ):
                return True
    return False


class SemanticIndex:
    """Owns the derived semantic vectors and the fallback candidate search."""

    def __init__(
        self,
        database: SearchDatabase,
        provider: SemanticProvider | None = None,
    ) -> None:
        self.database = database
        self.provider = provider or SemanticProvider()

    # -- dirty tracking ------------------------------------------------------

    def mark_dirty(self) -> None:
        """Mark the index stale (the indexer calls this after a pass)."""
        with closing(self.database.connect()) as connection:
            connection.execute(
                "INSERT INTO document_semantic_metadata (key, value)"
                " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (_DIRTY_KEY, "1"),
            )
            connection.commit()

    def is_dirty(self) -> bool:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT value FROM document_semantic_metadata WHERE key = ?",
                (_DIRTY_KEY,),
            ).fetchone()
        return row is not None and str(row["value"]) == "1"

    # -- rebuild -------------------------------------------------------------

    def rebuild(self) -> int:
        """Full rebuild from canonical content; returns the document count.

        Reads every document, computes n-grams and corpus idf, weights the
        vectors, and writes them in one transaction. The previous vectors
        are replaced, so a rebuild is always a clean full rewrite.
        """
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT d.id, d.name, f.content, d.content_hash"
                " FROM documents d"
                " JOIN documents_fts f ON f.document_id = d.id"
            ).fetchall()
        documents = [
            (str(row["id"]), str(row["name"]), row["content"], row["content_hash"])
            for row in rows
        ]
        n_docs = len(documents)
        provider = self.provider

        # Document frequency per n-gram across the whole corpus.
        df: dict[str, int] = {}
        per_doc: list[tuple[str, str, dict[str, int], list[str], str | None]] = []
        for doc_id, name, content, content_hash in documents:
            counts = provider.embed(name, content)
            words = content_words(name, content)
            per_doc.append((doc_id, name, counts, words, content_hash))
            for ngram in counts:
                df[ngram] = df.get(ngram, 0) + 1

        idf = {ngram: provider.idf(n_docs, freq) for ngram, freq in df.items()}

        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN")
            try:
                connection.execute("DELETE FROM document_semantic_terms")
                connection.execute("DELETE FROM document_semantic")
                term_rows: list[tuple[str, str, float, float, int, int]] = []
                doc_rows: list[tuple[str, int, int, float, str, str | None]] = []
                for doc_id, _name, counts, words, content_hash in per_doc:
                    weights = {
                        ngram: (1.0 + math.log(count)) * idf[ngram]
                        for ngram, count in counts.items()
                    }
                    norm = math.sqrt(sum(w * w for w in weights.values()))
                    if norm == 0.0:
                        norm = 1.0
                    for ngram, weight in weights.items():
                        term_rows.append(
                            (ngram, doc_id, weight, idf[ngram],
                             NGRAM_VERSION, NGRAM_PREPROCESSING_VERSION)
                        )
                    doc_rows.append(
                        (doc_id, NGRAM_VERSION, NGRAM_PREPROCESSING_VERSION,
                         norm, json.dumps(words), content_hash)
                    )
                connection.executemany(
                    "INSERT INTO document_semantic_terms (ngram, document_id,"
                    " weight, idf, version, preprocessing_version)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    term_rows,
                )
                connection.executemany(
                    "INSERT INTO document_semantic (document_id, version,"
                    " preprocessing_version, norm, words, content_hash)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    doc_rows,
                )
                connection.execute(
                    "INSERT INTO document_semantic_metadata (key, value)"
                    " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (_DIRTY_KEY, "0"),
                )
                connection.execute(
                    "INSERT INTO document_semantic_metadata (key, value)"
                    " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (_COUNT_KEY, str(n_docs)),
                )
                connection.execute(
                    "INSERT INTO document_semantic_metadata (key, value)"
                    " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (_N_KEY, str(provider.n)),
                )
                connection.execute(
                    "INSERT INTO document_semantic_metadata (key, value)"
                    " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (_VERSION_KEY, str(NGRAM_VERSION)),
                )
                connection.execute(
                    "INSERT INTO document_semantic_metadata (key, value)"
                    " VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (_PREPROCESSING_KEY, str(NGRAM_PREPROCESSING_VERSION)),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return n_docs

    def ensure_fresh(self) -> None:
        """Rebuild if the indexer marked the index dirty (lazy rebuild)."""
        if self.is_dirty():
            self.rebuild()

    # -- removal -------------------------------------------------------------

    def remove_all(self) -> int:
        """Delete every semantic row; returns how many documents were removed.

        Removable derived data: after this the search is exactly the lexical
        engine, and a later rebuild repopulates the vectors.
        """
        with closing(self.database.connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM document_semantic"
            ).fetchone()[0]
            connection.execute("DELETE FROM document_semantic_terms")
            connection.execute("DELETE FROM document_semantic")
            connection.execute("DELETE FROM document_semantic_metadata")
            connection.commit()
        return int(count)

    # -- fallback candidate search -------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        threshold: float = MIN_SIMILARITY,
    ) -> list[tuple[str, float]]:
        """Semantic candidates for ``query`` above ``threshold``, best first.

        Only called when the lexical engine returned nothing. Returns
        ``(document_id, similarity)`` pairs. A query whose best overlap is
        incidental (below ``threshold``) returns nothing — the "must
        retrieve nothing" contract is preserved.
        """
        query_counts = self.provider.embed_query(query)
        if not query_counts:
            return []
        query_idf = {
            ngram: self.provider.idf(self._count(), df)
            for ngram, df in self._query_df(query_counts).items()
        }
        query_weights = {
            ngram: (1.0 + math.log(count)) * query_idf.get(ngram, 0.0)
            for ngram, count in query_counts.items()
            if query_idf.get(ngram, 0.0) > 0.0
        }
        if not query_weights:
            return []
        query_norm = math.sqrt(sum(w * w for w in query_weights.values()))
        if query_norm == 0.0:
            return []
        # Precision gate: the query must share at least one content word
        # with the document. Without this, a nonsense query matches a
        # document on incidental sub-word fragments (the "nad"/"ada" of
        # "nada") — the failure the fixed threshold could not survive.
        query_words = set(content_words("", query))
        if not query_words:
            return []

        with closing(self.database.connect()) as connection:
            return self._score_candidates(
                connection, query_weights, query_norm, limit, threshold,
                query_words=query_words,
            )

    def _query_df(self, query_counts: dict[str, int]) -> dict[str, int]:
        """Document frequency of each query n-gram (for idf)."""
        placeholders = ",".join("?" for _ in query_counts)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"SELECT ngram, COUNT(*) AS df FROM document_semantic_terms"
                f" WHERE ngram IN ({placeholders}) GROUP BY ngram",
                list(query_counts),
            ).fetchall()
        return {str(row["ngram"]): int(row["df"]) for row in rows}

    def _count(self) -> int:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT value FROM document_semantic_metadata WHERE key = ?",
                (_COUNT_KEY,),
            ).fetchone()
        return int(row["value"]) if row else 0

    def _score_candidates(
        self,
        connection,
        query_weights: dict[str, float],
        query_norm: float,
        limit: int,
        threshold: float,
        query_words: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Cosine similarity per candidate, from the inverted postings.

        When ``query_words`` is given, a candidate must also share at
        least one content word with the query (the precision gate).
        """
        placeholders = ",".join("?" for _ in query_weights)
        rows = connection.execute(
            "SELECT ngram, document_id, weight FROM document_semantic_terms"
            f" WHERE ngram IN ({placeholders})",
            list(query_weights),
        ).fetchall()
        dots: dict[str, float] = {}
        for row in rows:
            ngram = str(row["ngram"])
            doc_id = str(row["document_id"])
            dots[doc_id] = dots.get(doc_id, 0.0) + query_weights[ngram] * float(
                row["weight"]
            )
        if not dots:
            return []
        doc_ids = list(dots)
        norm_placeholders = ",".join("?" for _ in doc_ids)
        norms = {
            str(row["document_id"]): float(row["norm"])
            for row in connection.execute(
                f"SELECT document_id, norm FROM document_semantic"
                f" WHERE document_id IN ({norm_placeholders})",
                doc_ids,
            ).fetchall()
        }
        # Load the candidates' content words for the precision gate.
        doc_words: dict[str, set[str]] = {}
        if query_words:
            for row in connection.execute(
                f"SELECT document_id, words FROM document_semantic"
                f" WHERE document_id IN ({norm_placeholders})",
                doc_ids,
            ).fetchall():
                doc_words[str(row["document_id"])] = set(
                    json.loads(str(row["words"]))
                )
        scored: list[tuple[str, float]] = []
        for doc_id, dot in dots.items():
            if query_words and not _shares_word(
                query_words, doc_words.get(doc_id, set())
            ):
                continue
            norm = norms.get(doc_id, 0.0)
            if norm <= 0.0:
                continue
            similarity = dot / (query_norm * norm)
            if similarity >= threshold:
                scored.append((doc_id, similarity))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]
