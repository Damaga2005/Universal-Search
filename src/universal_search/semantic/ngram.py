"""The semantic "model": a character n-gram TF-IDF embedder (phase 026).

This is the only local semantic technique that needs no runtime dependency,
no network and no model download. A document (or query) is represented by
the character 3-grams of its name and content, weighted by TF-IDF. Cosine
similarity over those vectors is a dependency-free approximation of
semantic matching: it catches morphological variants (``receta`` /
``recetas``), accent-folded overlaps (``polarizacion`` / ``polarización``)
and partial term overlap in paraphrases — the failure classes the lexical
engine cannot reach.

It is honest about what it is not: it is fuzzy lexical matching, not a
learned embedding model. Pure synonyms with no shared surface form (``BJT``
vs ``transistor de union bipolar``) remain out of reach — closing that gap
would require a learned model, which needs a runtime dependency, a model
download (network) and a license, all of which the program boundaries forbid
unless a measured requirement justifies them. Phase 026 measured that they do
not.

The embedder is versioned (``NGRAM_VERSION``) and its preprocessing is
versioned (``NGRAM_PREPROCESSING_VERSION``): incompatible derived data is
rebuilt, never silently reinterpreted.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Versioned dimensions of the embedder. Bump NGRAM_VERSION to change the
# model contract; bump NGRAM_PREPROCESSING_VERSION to change how text is
# normalized before n-gramming. Both are stored per row so a rebuild can
# detect incompatibility.
# 2: smoothed idf (phase 029). Version 1 used log(n/df), which is zero for
# every n-gram present in all documents and disabled the layer entirely on a
# one-document index.
NGRAM_VERSION = 2
NGRAM_PREPROCESSING_VERSION = 1

# Character n-gram size. 3-grams are the shortest that still discriminate
# word fragments; 4-grams would be more precise but miss more morphological
# variants (the accent and plural cases the layer exists to catch).
NGRAM_SIZE = 3

# Bound on how many n-grams are kept per document, most distinctive first.
# This bounds the derived storage per document independently of document
# size — the same bounding discipline as the graph's term vector.
MAX_NGRAMS_PER_DOC = 200

# A content word is a token of at least this many characters. Shorter
# tokens ("no", "de", "el", "se") are function words that appear in every
# document and would make the precision gate meaningless.
MIN_CONTENT_WORD = 3

# Bound on how many content words are stored per document (most frequent
# first), bounding the precision-gate storage.
MAX_CONTENT_WORDS = 200

# Word tokens, mirroring ranking.tokens / the query lexer: Unicode letters,
# digits and underscore. The text is reduced to word tokens joined by single
# spaces before n-gramming, so n-grams never straddle a word boundary in a
# way that depends on punctuation.
WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalized(text: str) -> str:
    """Word tokens of ``text`` case-folded and joined by single spaces."""
    return " ".join(WORD_RE.findall(text.casefold()))


def ngram_counts(text: str, n: int = NGRAM_SIZE) -> Counter:
    """Character ``n``-gram counts of ``text`` (word-boundary safe)."""
    normalized = _normalized(text)
    if len(normalized) < n:
        return Counter()
    return Counter(normalized[i:i + n] for i in range(len(normalized) - n + 1))


def content_text(name: str, content: str | None) -> str:
    """The text a document is embedded on: its name and its content.

    Name + content only — never the path. Path n-grams pollute the
    similarity (a ``zzz-almacen`` folder makes ``zzz no existe`` look
    relevant, and the ``diagrama`` tie flips on path length), which the
    phase-026 measurement caught.
    """
    return f"{name} {content or ''}"


def content_words(
    name: str,
    content: str | None,
    *,
    min_length: int = MIN_CONTENT_WORD,
    limit: int = MAX_CONTENT_WORDS,
) -> list[str]:
    """Distinct content words of a document, most frequent first.

    The precision gate: a semantic candidate must share at least one of
    these with the query. Real semantic matches (synonyms, paraphrases)
    share content words; a nonsense query shares only incidental sub-word
    fragments (``nad``/``ada`` from ``nada``) and is correctly rejected.
    """
    counts: Counter = Counter(
        token
        for token in WORD_RE.findall(content_text(name, content).casefold())
        if len(token) >= min_length
    )
    return [word for word, _ in counts.most_common(limit)]


class SemanticProvider:
    """Versioned char n-gram TF-IDF embedder — the local semantic "model".

    The provider is stateless and deterministic: the same text always
    produces the same vector. All corpus-level state (document frequency,
    idf, norms) lives in :class:`~universal_search.semantic.index.SemanticIndex`,
    not here, so the provider can never go stale.
    """

    def __init__(
        self,
        *,
        n: int = NGRAM_SIZE,
        max_ngrams: int = MAX_NGRAMS_PER_DOC,
    ) -> None:
        self.n = n
        self.max_ngrams = max_ngrams

    def embed(self, name: str, content: str | None) -> dict[str, int]:
        """Raw n-gram counts of a document, bounded to the most frequent.

        The bound keeps the derived storage flat per document. Frequency is
        the selection criterion because the index rebuild computes idf over
        the whole corpus anyway, so the most frequent n-grams are the ones
        most likely to be distinctive after idf weighting.
        """
        counts = ngram_counts(content_text(name, content), self.n)
        if not counts:
            return {}
        kept = counts.most_common(self.max_ngrams)
        return dict(kept)

    def embed_query(self, query: str) -> dict[str, int]:
        """Raw n-gram counts of a query (unbounded — queries are short)."""
        return dict(ngram_counts(query, self.n))

    @staticmethod
    def idf(n_docs: int, df: int) -> float:
        """Smoothed inverse document frequency, always strictly positive.

        The textbook ``log(n / df)`` is exactly zero for an n-gram that
        appears in *every* document, which silently switched the whole
        semantic layer off for a one-document index (a real state right
        after a fresh install) and made a morphological query retrieve
        nothing. The smoothed form is monotone in ``df`` — it ranks
        n-grams exactly as before — but never collapses to zero.

        Bumping ``NGRAM_VERSION`` is mandatory when this changes: derived
        rows store the version, so incompatible vectors are rebuilt
        instead of being reinterpreted.
        """
        if n_docs <= 0 or df <= 0:
            return 0.0
        return math.log((n_docs + 1) / (df + 1)) + 1.0
