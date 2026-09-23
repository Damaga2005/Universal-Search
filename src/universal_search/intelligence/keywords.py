"""Meaningful terms, keywords and lightweight co-occurrence (spec 014).

Two bounded, deterministic artifacts per document:

* **terms** — the most frequent content words with their counts, after
  dropping function words, very short tokens and pure numbers. This is
  the vector document similarity is computed on.
* **pairs** — the most frequent co-occurring term pairs inside a small
  sliding window, restricted to terms that survived into the bounded
  vocabulary. Restricting the pairs to that vocabulary is what keeps the
  artifact small: it is a summary of the document's own concepts, not a
  general n-gram index.

Ties are broken alphabetically, so the same bytes always produce the same
keywords in the same order. Tokenization reuses
:func:`universal_search.index.ranking.tokens` — the same word shape FTS5's
``unicode61`` tokenizer produces, so a keyword is always a word the index
can match.
"""

from collections import Counter
from collections.abc import Iterable, Sequence

from universal_search.index.ranking import tokens
from universal_search.intelligence.language import STOPWORDS

# Bounded storage: a handful of terms per document, not a full bag of words.
MAX_TERMS = 24
MAX_PAIRS = 16

# How many neighbouring meaningful terms count as "co-occurring".
WINDOW = 4

# One- and two-letter tokens are noise in prose ("de", "la", "x", "id").
MIN_TERM_LENGTH = 3


def meaningful_terms(words: Sequence[str]) -> tuple[tuple[str, int], ...]:
    """Top content words with counts, ordered by count then alphabetically.

    ``words`` must already be case-folded and tokenized; both callers in
    this package fold before calling.
    """
    counts = Counter(
        word
        for word in words
        if len(word) >= MIN_TERM_LENGTH
        and word not in STOPWORDS
        and not word.isdigit()
    )
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ordered[:MAX_TERMS])


def co_occurrences(
    words: Sequence[str], vocabulary: Iterable[str]
) -> tuple[tuple[str, str], ...]:
    """Most frequent co-occurring pairs among ``vocabulary`` terms."""
    allowed = set(vocabulary)
    if len(allowed) < 2:
        return ()
    counts: Counter[tuple[str, str]] = Counter()
    window: list[str] = []
    for word in words:
        if word not in allowed:
            continue
        window.append(word)
        if len(window) > WINDOW:
            window.pop(0)
        for first in range(len(window)):
            for second in range(first + 1, len(window)):
                left, right = window[first], window[second]
                if left == right:
                    continue
                pair = (left, right) if left < right else (right, left)
                counts[pair] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(pair for pair, _ in ordered[:MAX_PAIRS])


def analyze_words(
    text: str,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, str], ...]]:
    """Terms and co-occurrence pairs for ``text`` in one pass."""
    words = tokens(text.casefold())
    terms = meaningful_terms(words)
    pairs = co_occurrences(words, [term for term, _ in terms])
    return terms, pairs
