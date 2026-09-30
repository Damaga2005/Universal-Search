"""Robust search: typo and partial-word tolerance, locally (phase 031).

Two modules with two jobs, and the separation is the point:

* :mod:`universal_search.fuzzy.trigrams` blocks. Cheap, bounded per document,
  allowed to be wrong in the "too many candidates" direction.
* :mod:`universal_search.fuzzy.verify` decides. Every candidate is checked
  against the document's real text with a substring test or a bounded edit
  distance.

:class:`FuzzySearchEngine` wraps the authoritative lexical engine and only
consults the layer when that engine returns nothing, so exact matches, phrases,
filters and query operators are unaffected.
"""

from __future__ import annotations

from universal_search.fuzzy.engine import FuzzySearchEngine, engine_for
from universal_search.fuzzy.index import (
    FUZZY_PREPROCESSING_VERSION,
    FUZZY_VERSION,
    FuzzyIndex,
)
from universal_search.fuzzy.trigrams import (
    MAX_CANDIDATES,
    MAX_TRIGRAMS_PER_DOC,
    MIN_TOKEN_OVERLAP,
    MIN_TOKEN_CHARS,
    MIN_TRIGRAMS_FOR_DOCUMENT,
    NGRAM_SIZE,
    fold,
    query_trigrams,
    selective_trigrams,
    tokens_of,
    trigram_counts,
)
from universal_search.fuzzy.verify import (
    MAX_VERIFY_CHARS,
    damerau_levenshtein,
    edit_budget,
    is_nonsense,
    resolve_query,
    resolve_token,
    resolve_token_folded,
    resolve_tokens,
)
from universal_search.fuzzy.suggest import (
    MAX_SUGGESTIONS,
    QuerySuggester,
    Suggestion,
    correct_token,
    indexed_vocabulary,
)

__all__ = [
    "FUZZY_PREPROCESSING_VERSION",
    "FUZZY_VERSION",
    "FuzzyIndex",
    "FuzzySearchEngine",
    "MAX_CANDIDATES",
    "MAX_SUGGESTIONS",
    "MAX_TRIGRAMS_PER_DOC",
    "MAX_VERIFY_CHARS",
    "MIN_TOKEN_OVERLAP",
    "MIN_TOKEN_CHARS",
    "MIN_TRIGRAMS_FOR_DOCUMENT",
    "NGRAM_SIZE",
    "QuerySuggester",
    "Suggestion",
    "correct_token",
    "damerau_levenshtein",
    "edit_budget",
    "engine_for",
    "fold",
    "indexed_vocabulary",
    "is_nonsense",
    "query_trigrams",
    "resolve_query",
    "resolve_token",
    "resolve_token_folded",
    "resolve_tokens",
    "selective_trigrams",
    "tokens_of",
    "trigram_counts",
]
