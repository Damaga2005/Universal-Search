"""Phase 031, part 1: the blocking step.

Character trigrams with a per-document budget. This module is a **cheap
filter**: it answers "which documents could possibly contain this string?" and
it is allowed to be wrong in that direction only. Nothing it produces is ever
returned to a user — :mod:`universal_search.fuzzy.verify` makes every decision.

The bound is per document, not per byte: a 2 MB file and a 40 B file both
cost at most 64 rows. Without that property a single large file could add
millions of derived rows, which is why a second FTS5 ``trigram`` table was
rejected in the phase-031 design.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter


# Character trigrams. Two is too coarse (too many false collisions) and four
# misses the "transsistor" class of typos, so three is the same choice the
# semantic layer of phase 026 already made and measured.
NGRAM_SIZE = 3

# Upper bound on distinct trigrams kept per document.
MAX_TRIGRAMS_PER_DOC = 64

# Upper bound on words whose trigrams make up that fingerprint. This is the
# knob that decides *recall*: how much of a document's vocabulary a query can
# block on. 32 words of ~8 characters fill the 64 trigram slots, so the trigram
# cap is the binding budget and this one decides coverage, not cost.
MAX_FINGERPRINT_WORDS = 32

# A token shorter than this cannot be verified: too many words are within one
# edit of a two-letter string.
MIN_TOKEN_CHARS = 3

# Upper bound on candidates that reach verification. Below this the blocking
# step is too eager; above it a bad query costs real work.
MAX_CANDIDATES = 50

# Documents whose fingerprint is too small to discriminate are not indexed at
# all: a document that shares trigrams with everything tells us nothing.
MIN_TRIGRAMS_FOR_DOCUMENT = 8

# Minimum share of a *token's* trigrams a document must hold to become a
# candidate. Deliberately permissive: this number only decides how many
# documents reach the verifier, and the verifier is the one that decides. The
# first implementation applied this to the whole query, which threw away
# correct answers: a two-word query where one word is misspelled can never
# reach a high global overlap, and the phase-031 measurement caught the
# verifier saying yes to candidates the threshold had already discarded.
#
# A transposition in the middle of a word is the hard case for trigram
# blocking: swapping two adjacent characters changes every trigram spanning
# them, and for a 7-character word that is 4 of its 5. Measured on the
# labelled corpus, "azarfan" for "azafran" shares exactly one trigram (0.2)
# and is therefore not blocked.
#
# The floor is 0.2 — one shared trigram is admitted — because the verifier,
# not this number, decides. Lowering it costs candidate-list length and
# therefore latency (gate T5), never correctness (gate T2). Mid-word
# transpositions in words of 7 characters or fewer are a documented limit of
# the layer, not a bug in it.
MIN_TOKEN_OVERLAP = 0.2

WORD_RE = re.compile(r"\w+", re.UNICODE)


def fold(text: str) -> str:
    """Case-folded and accent-stripped.

    Folding accents here is what makes ``polarisacion`` find
    ``polarización`` without a second mechanism. The lexical engine already
    folds diacritics in FTS5, so this layer agrees with it instead of
    fighting it.
    """
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def trigram_counts(text: str, n: int = NGRAM_SIZE) -> Counter:
    """Character trigram counts of already-folded text."""
    normalized = fold(text)
    if len(normalized) < n:
        return Counter()
    return Counter(
        normalized[i:i + n] for i in range(len(normalized) - n + 1)
    )


def query_trigrams(text: str, n: int = NGRAM_SIZE) -> set[str]:
    """Distinct trigrams of a query, bounded by the query's own length."""
    return set(trigram_counts(text, n))


def selective_trigrams(
    text: str,
    document_frequency: dict[str, int],
    *,
    limit: int = MAX_TRIGRAMS_PER_DOC,
    minimum: int = MIN_TRIGRAMS_FOR_DOCUMENT,
    max_words: int = MAX_FINGERPRINT_WORDS,
) -> dict[str, float]:
    """The trigrams worth indexing for one document, as a *word* fingerprint.

    An earlier version of this function kept the 64 most selective trigrams of
    the document, and it was wrong in a way only a test could show: on a
    multi-document corpus the trigrams of a *common* word ("transistor",
    present in two files) lose the selectivity contest to some one-off
    trigram, get dropped, and then a query for that word blocks nothing. The
    fingerprint has to cover the words that identify the document, not the
    trigrams that happen to be rare.

    So the unit of selection is the **word**: take the most distinctive words
    of the document, then store all of their trigrams. A query that matches
    one of those words shares trigrams with it by construction, and the budget
    is still hard: at most ``max_words`` words and at most ``limit`` trigrams.
    """
    words = WORD_RE.findall(fold(text))
    if not words:
        return {}
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for word in words:
        if word in seen or len(word) < MIN_TOKEN_CHARS:
            continue
        seen.add(word)
        frequency = document_frequency.get(word, 1)
        scored.append((frequency, word))
    if not scored:
        return {}
    # Rarest word first, alphabetical among equals so the choice is stable and
    # not length-biased. Deliberately *not* longest-first: a 14-character word
    # would otherwise eat a quarter of the budget before any other word
    # contributed a single trigram.
    scored.sort()
    selected = [word for _frequency, word in scored[:max_words]]

    # Round-robin over the selected words so the budget is spread across the
    # document's vocabulary instead of being spent on its first few words.
    # Trigrams keep their *positional* order (a Counter iterates in insertion
    # order), which matters: a prefix query such as "transisto" shares the
    # leading trigrams of "transistor", so the leading trigrams are the ones
    # worth spending the budget on. Sorting them alphabetically would spend it
    # on arbitrary ones.
    per_word = [list(trigram_counts(word)) for word in selected]
    per_word = [ngrams for ngrams in per_word if ngrams]
    kept: dict[str, float] = {}
    position = 0
    while len(kept) < limit and any(
        position < len(ngrams) for ngrams in per_word
    ):
        for index, word in enumerate(selected):
            ngrams = per_word[index] if index < len(per_word) else []
            if position >= len(ngrams):
                continue
            ngram = ngrams[position]
            if ngram not in kept:
                kept[ngram] = 1.0 / max(
                    1, document_frequency.get(word, 1)
                )
        position += 1
    return _finished(kept, minimum)


def _finished(kept: dict[str, float], minimum: int) -> dict[str, float]:
    """Below ``minimum`` distinct trigrams the document is not indexed.

    Too small to discriminate: it would land in the candidate list of every
    query that happens to share a trigram, and cost verification work for
    nothing.
    """
    if len(kept) < minimum:
        return {}
    return kept


def overlap(query_ngrams: set[str], document_ngrams: set[str]) -> float:
    """Share of the query's trigrams present in the document, 0.0 to 1.0.

    A filter score only. A document can score 1.0 here and still be rejected by
    the verifier, which is the whole design.
    """
    if not query_ngrams:
        return 0.0
    return len(query_ngrams & document_ngrams) / len(query_ngrams)


def tokens_of(text: str) -> list[str]:
    """Word tokens of a query or text, folded, in order."""
    return WORD_RE.findall(fold(text))
